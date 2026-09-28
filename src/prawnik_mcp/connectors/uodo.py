"""UODO connector: decisions of the President of the Personal Data Protection Office (orzeczenia.uodo.gov.pl).

The portal (react-router SSR) exposes keyless route-data endpoints; we use two, both verified 2026-09-28:
- search: `GET /search.data?dcr=rodo&q=<text>&rn=<case number>&dtps=<from>&dtpe=<to>&page=N`
  (`dtps`/`dtpe` = publication date window, ISO dates; 10 items per page);
- decision: `GET /document/{urn}/content.data` (metadata + HTML body in one response; stored as the snapshot).
The human-facing page is `/document/{urn}/content` (kept as `original_url`).

Document ids are `uodo:<year>:<code>`, the tail of the decision URN (`urn:ndoc:gov:pl:uodo:2022:dkn_5112_28`
-> `uodo:2022:dkn_5112_28`); case numbers (e.g. "DKN.5112.28.2022") go to `case_numbers`.

Endpoint map from the ledger in kio-orzeczenia-mcp/SOURCES.md
(https://github.com/matematicsolutions/kio-orzeczenia-mcp, Copyright MateMatic, Apache License 2.0).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.uodo import (
    BASE_URL,
    COURT_NAME,
    COURT_TYPE,
    ID_RE,
    PARSER_VERSION,
    SIGNATURE_RE,
    UodoNotFound,
    UodoSearchPage,
    content_data_url,
    id_to_urn,
    page_url,
    parse_content,
    parse_search,
    parse_uodo_decision,
)
from prawnik_mcp.store import Store

SOURCE_ID = "uodo"
ACCEPT = "text/x-script, application/json;q=0.9, */*;q=0.5"
CONTENT_TYPE = "text/x-script; charset=utf-8"
DEFAULT_DECREE = "rodo"  # GDPR-era decisions; other values of `dcr` not verified
MAX_SEARCH_PAGES = 3


def search_url(*, query: str | None = None, case_number: str | None = None, published_from: str | None = None,
               published_to: str | None = None, page: int = 1, decree: str | None = DEFAULT_DECREE) -> str:
    params: dict[str, str | int] = {}
    if decree:
        params["dcr"] = decree
    if query:
        params["q"] = query
    if case_number:
        params["rn"] = case_number
    if published_from:
        params["dtps"] = str(published_from)[:10]
    if published_to:
        params["dtpe"] = str(published_to)[:10]
    params["page"] = page
    return f"{BASE_URL}/search.data?{urlencode(params)}"


def search_page(client: PoliteClient, url: str) -> UodoSearchPage:
    return parse_search(client.get(url, accept=ACCEPT).content)


def _listing_for(store: Store, doc_id: str) -> dict | None:
    doc = store.get_document(doc_id)
    return (doc.metadata.get("listing") or None) if doc else None


def ingest_decision(store: Store, content: bytes, url: str, fetched_at: datetime | None,
                    listing: dict | None = None, content_type: str = CONTENT_TYPE) -> Judgment:
    parse_content(content)  # validate (UodoNotFound / ValueError) before anything is written
    snap = store.put_snapshot(SOURCE_ID, url, content, content_type, parser_version=PARSER_VERSION,
                              fetched_at=fetched_at)
    judgment, doc = parse_uodo_decision(content, snapshot_id=snap.snapshot_id, sha256=snap.sha256,
                                        fetched_at=snap.fetched_at, listing=listing)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


def fetch_decision(store: Store, client: PoliteClient, doc_tail: str, *, force: bool = False,
                   listing: dict | None = None) -> tuple[Judgment, bool]:
    """Returns (judgment, fetched_now). A stored snapshot is re-parsed instead of re-fetched.

    The portal answers 200 with an empty document for an unknown URN; that is raised as
    `NotFoundUpstream` and nothing is stored."""
    urn = id_to_urn(doc_tail)
    url = content_data_url(urn)
    listing = listing or _listing_for(store, f"uodo:{doc_tail}")
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            return ingest_decision(store, content, url, snap.fetched_at, listing, snap.content_type), False
    r = client.get(url, accept=ACCEPT)
    try:
        parse_content(r.content)
    except UodoNotFound:
        raise NotFoundUpstream(url, "portal zwrócił pusty dokument (status 'unknown') dla tego URN", r.status) from None
    return ingest_decision(store, r.content, r.url, r.fetched_at, listing, r.content_type), True


def _fixture_dir(fixtures: Path) -> Path:
    return fixtures if fixtures.name == SOURCE_ID else fixtures / SOURCE_ID


def _in_window(issue: str | None, lo: str | None, hi: str | None) -> bool:
    if not (lo or hi):
        return True
    if not issue:
        return False
    return (not lo or issue >= str(lo)[:10]) and (not hi or issue <= str(hi)[:10])


# --------------------------------------------------------------------------- connector


class UodoConnector(BaseConnector):
    """Decisions of the President of UODO (GDPR enforcement)."""

    source_id = "uodo"
    supports_search = True
    supports_fetch = True

    # ------------------------------------------------------------------ search
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live search (listing metadata only; `fetch` stores the full text).

        filters: case_number (-> rn; strings that are not UODO case numbers return []),
        published_from/published_to (-> dtps/dtpe, server-side), date_from/date_to (decision date,
        applied locally to the returned page only), decree (default "rodo"), court_type (other than
        DATA_PROTECTION_AUTHORITY returns [])."""
        f = filters or {}
        if f.get("court_type") and str(f["court_type"]).upper() != COURT_TYPE:
            return []
        case = None
        if f.get("case_number"):
            m = SIGNATURE_RE.search(str(f["case_number"]).upper())
            if not m:
                return []
            case = m.group(0)
        hits: list[RemoteHit] = []
        page = 1
        while len(hits) < limit and page <= MAX_SEARCH_PAGES:
            url = search_url(query=None if case else (query or None), case_number=case,
                             published_from=f.get("published_from"), published_to=f.get("published_to"),
                             page=page, decree=f.get("decree", DEFAULT_DECREE))
            res = search_page(client, url)
            for it in res.items:
                if len(hits) >= limit:
                    break
                if not _in_window(it.issue_date, f.get("date_from"), f.get("date_to")):
                    continue
                hits.append(RemoteHit(
                    document_id=f"uodo:{it.doc_id}", kind="decision",
                    title=f"{it.name or COURT_NAME}, {it.issue_date or 'data niepewna'}",
                    snippet=it.subject[:800], original_url=page_url(it.urn),
                    metadata={"court": COURT_NAME, "court_type": COURT_TYPE, "case_numbers": it.case_numbers,
                              "judgment_date": it.issue_date, "urn": it.urn, "dates": it.dates,
                              "keywords": it.keywords, "total": res.total, "source": SOURCE_ID}))
            if page >= res.pages or not res.items:
                break
            page += 1
        return hits

    # ------------------------------------------------------------------ fetch
    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        if not document_id.startswith("uodo:") or not ID_RE.fullmatch(document_id[5:].lower()):
            return None
        j, _ = fetch_decision(store, client, document_id[5:].lower(), force=force)
        return j.document_id

    # ------------------------------------------------------------------ default sample
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        """Small sample from catalog defaults: `queries = [{query, since, until}]` (publication window), `max_total`."""
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        queries = d.get("queries") or []
        if not queries:
            res.warnings.append("no default scope in the catalog ([sources.uodo.defaults]); nothing fetched")
            res.ok = True
            return res
        max_total = min(limit or d.get("max_total", 10), d.get("max_total", 10))
        fetched, reused, seen = 0, 0, set()
        for q in queries:
            try:
                page = search_page(client, search_url(query=q.get("query"), published_from=q.get("since"),
                                                      published_to=q.get("until")))
            except Exception as e:  # noqa: BLE001 - other queries may still work
                res.errors.append(f"search {q}: {type(e).__name__}: {e}")
                continue
            for it in page.items:
                if len(seen) >= max_total:
                    break
                if it.doc_id in seen:
                    continue
                seen.add(it.doc_id)
                try:
                    j, now = fetch_decision(store, client, it.doc_id, force=force, listing=it.listing())
                except NotFoundUpstream:
                    res.warnings.append(f"uodo:{it.doc_id}: listed in search but not available upstream; not stored")
                    continue
                except Exception as e:  # noqa: BLE001 - one bad decision must not stop the sample
                    res.errors.append(f"uodo:{it.doc_id}: {type(e).__name__}: {e}")
                    continue
                fetched, reused = fetched + now, reused + (not now)
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
        res.counts = {"fetched": fetched, "reused_checkpoint": reused}
        success = bool(fetched or reused)
        res.ok = success and not res.errors
        self.record(store, success=success, partial=bool(res.errors), offline=False)
        return res

    # ------------------------------------------------------------------ offline
    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        """Build from recorded `uodo/content_<year>_<code>.json`; `uodo/search_*.json` add listing metadata."""
        res = SourceSyncResult(source_id=self.source_id)
        base = _fixture_dir(fixtures)
        listings: dict[str, dict] = {}
        for p in sorted(base.glob("search_*.json")):
            try:
                for it in parse_search(p.read_bytes()).items:
                    listings.setdefault(it.doc_id, it.listing())
            except Exception as e:  # noqa: BLE001
                res.warnings.append(f"{p.name}: {type(e).__name__}: {e}")
        n = 0
        for p in sorted(base.glob("content_*.json")):
            m = re.fullmatch(r"content_(\d{4})_([a-z0-9_]+)\.json", p.name)
            if not m:
                continue
            doc_tail = f"{m.group(1)}:{m.group(2)}"
            try:
                j = ingest_decision(store, p.read_bytes(), content_data_url(id_to_urn(doc_tail)), mtime(p),
                                    listings.get(doc_tail))
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"decisions": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    # ------------------------------------------------------------------ bulk
    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Paged import with checkpoint/resume.

        params: query (full text), case_number, since/until = **publication** date window (dtps/dtpe; the
        decision-date filter of the portal is not verified). The portal orders a non-text scope by decision
        date, newest first, so new decisions shift pages: use a closed past window for stable paging.
        Cursor = next page number; a page is committed only when all its items were handled.
        """
        res = SourceSyncResult(source_id=self.source_id)
        scope = scope_key(self.source_id, {"mode": "search", **params})
        state = store.get_sync_state(self.source_id, scope) if limits.resume else None
        if state and state["done"]:
            res.ok = True
            res.warnings.append(f"scope {scope} already complete ({state['items']} items); use --no-resume to redo")
            return res
        page = int(state["cursor"]) if state and state["cursor"] else 1
        items_total = state["items"] if state else 0
        stored_now = 0
        last = False
        while True:
            if limits.limit is not None and stored_now >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            url = search_url(query=params.get("query"), case_number=params.get("case_number"),
                             published_from=params.get("since"), published_to=params.get("until"), page=page,
                             decree=params.get("decree", DEFAULT_DECREE))
            try:
                result = search_page(client, url)
            except Exception as e:  # noqa: BLE001 - keep checkpoint, report
                res.errors.append(f"page {page}: {type(e).__name__}: {e}")
                break
            if not result.items:
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=True, items=items_total,
                                     bytes_=store.data_size_bytes())
                break
            page_complete = True
            with store.batch():
                for it in result.items:
                    if limits.limit is not None and stored_now >= limits.limit:
                        page_complete = False  # resume must revisit the rest of this page
                        break
                    if limits.resume and store.get_judgment(f"uodo:{it.doc_id}") is not None:
                        continue
                    try:
                        fetch_decision(store, client, it.doc_id, force=not limits.resume, listing=it.listing())
                        stored_now += 1
                    except NotFoundUpstream:
                        res.warnings.append(f"uodo:{it.doc_id}: listed in search but empty upstream; not stored")
                    except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
                        res.errors.append(f"uodo:{it.doc_id}: {type(e).__name__}: {e}")
                if page_complete:
                    items_total += len(result.items)
                    page += 1
                last = page_complete and page > result.pages
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=last, items=items_total,
                                     bytes_=store.data_size_bytes())
            if progress:
                progress(f"uodo: next page {page}/{result.pages}, stored {stored_now} in this run")
            if not page_complete or last:
                break
        res.counts = {"stored": stored_now, "pages_done": page - 1}
        res.ok = not res.errors
        self.record(store, success=stored_now > 0 or res.ok, partial=bool(res.errors), offline=False)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {}).get("judgments", 0)
        return f"{n} UODO decisions stored locally (sample; orzeczenia.uodo.gov.pl)", []

