"""KIO (Krajowa Izba Odwoławcza) connector: public procurement appeal rulings from orzeczenia.uzp.gov.pl.

No public API: the UZP portal is ASP.NET MVC. Search is the AJAX endpoint `POST /Home/GetResults`
(form-urlencoded, 10 results per page); each ruling is stored from two pages fetched in full:
`GET /Home/Details/{id}` (metrics, 404 for unknown ids) and `GET /Home/ContentHtml/{id}` (text; answers
200 with an empty body for unknown ids, so the metrics page is always fetched first). Both raw
responses are kept as snapshots. Polite rate: catalog `rate_per_s` (≤1 req/s).

Portions adapted from kio-orzeczenia-mcp (https://github.com/matematicsolutions/kio-orzeczenia-mcp),
Copyright MateMatic (Wiesław Mazur), Apache License 2.0: endpoint map (DISCOVERY.md, client.py) and the
search form field mapping (`_search_form_data`). Rewritten for the sync PoliteClient and the local store.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.kio import (
    COURT_NAME,
    COURT_TYPE,
    DOC_TYPES,
    DOC_TYPES_PL,
    PARSER_VERSION,
    SEARCH_URL,
    KioSearchPage,
    content_url,
    details_url,
    fold,
    normalize_signature,
    parse_details,
    parse_kio_ruling,
    parse_search_results,
)
from prawnik_mcp.store import Store

SOURCE_ID = "kio"
ACCEPT_HTML = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
MIN_DATE = date(2004, 1, 1)  # UZP needs both ends of the "Dt" range
SORTS = ("rank", "date_asc", "date_desc")
MAX_SEARCH_PAGES = 3  # live search budget: at most 3 POSTs


def _iso(d: Any) -> date | None:
    if d is None or d == "":
        return None
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def search_form(*, phrase: str | None = None, signature: str | None = None, date_from: Any = None,
                date_to: Any = None, pzp_article: str | None = None, subject_index: str | None = None,
                page: int = 1, sort: str | None = None) -> dict[str, str]:
    """Form payload for `POST /Home/GetResults` (field names verified 2026-09-28).

    Phrase/Sign/Dt ("DD-MM-YYYY - DD-MM-YYYY", both ends required)/Art/ThIdx/Kind/Pg/Srt/CountStats;
    Fle (word inflection) and SCnt (search in full text) are checkboxes sent only when a phrase is given.
    """
    data: dict[str, str] = {"Kind": "KIO", "Pg": str(page), "CountStats": "True"}
    if phrase:
        data.update(Phrase=phrase, Fle="1", SCnt="1")
    if signature:
        data["Sign"] = signature
    lo, hi = _iso(date_from), _iso(date_to)
    if lo or hi:
        lo, hi = lo or MIN_DATE, hi or date.today()
        data["Dt"] = f"{lo:%d-%m-%Y} - {hi:%d-%m-%Y}"
    if pzp_article:
        data["Art"] = pzp_article
    if subject_index:
        data["ThIdx"] = subject_index
    if sort:
        if sort not in SORTS:
            raise ValueError(f"sort must be one of {SORTS}")
        data["Srt"] = sort
    return data


def search_page(client: PoliteClient, form: dict[str, str]) -> KioSearchPage:
    r = client.post(SEARCH_URL, data=form, accept=ACCEPT_HTML)
    return parse_search_results(r.content)


def ingest_ruling(store: Store, internal_id: str, details: bytes, details_url_: str, details_at: datetime | None,
                  content: bytes, content_url_: str, content_at: datetime | None,
                  details_type: str = "text/html; charset=utf-8", content_type: str = "text/html") -> Judgment:
    parse_details(details)  # validate before anything is written
    dsnap = store.put_snapshot(SOURCE_ID, details_url_, details, details_type,
                               parser_version=PARSER_VERSION, fetched_at=details_at)
    csnap = store.put_snapshot(SOURCE_ID, content_url_, content, content_type,
                               parser_version=PARSER_VERSION, fetched_at=content_at)
    judgment, doc = parse_kio_ruling(details, content, internal_id, snapshot_id=csnap.snapshot_id,
                                     sha256=csnap.sha256, fetched_at=csnap.fetched_at,
                                     details_snapshot_id=dsnap.snapshot_id)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


def fetch_ruling(store: Store, client: PoliteClient, internal_id: str, *, force: bool = False) -> tuple[Judgment, bool]:
    """Returns (judgment, fetched_now). Stored snapshots of both pages are re-parsed, not re-fetched.

    Nothing is stored unless both pages were fetched and the metrics page parsed: an unknown id gives
    `NotFoundUpstream` (404 on Details); an empty ContentHtml gives `SourceUnavailable`."""
    durl, curl = details_url(internal_id), content_url(internal_id)
    if not force:
        ds, cs = store.find_snapshot_by_url(durl), store.find_snapshot_by_url(curl)
        dbytes = store.read_snapshot_bytes(ds.snapshot_id) if ds else None
        cbytes = store.read_snapshot_bytes(cs.snapshot_id) if cs else None
        if ds and cs and dbytes is not None and cbytes is not None:
            j = ingest_ruling(store, internal_id, dbytes, durl, ds.fetched_at, cbytes, curl, cs.fetched_at,
                              ds.content_type, cs.content_type)
            return j, False
    d = client.get(durl, accept=ACCEPT_HTML)
    parse_details(d.content)  # a 200 without the metrics block is a layout change: stop before the 2nd request
    c = client.get(curl, accept=ACCEPT_HTML)
    j = ingest_ruling(store, internal_id, d.content, durl, d.fetched_at, c.content, curl, c.fetched_at,
                      d.content_type, c.content_type)
    return j, True


def _fixture_dir(fixtures: Path) -> Path:
    return fixtures if fixtures.name == SOURCE_ID else fixtures / SOURCE_ID


# --------------------------------------------------------------------------- connector


class KioConnector(BaseConnector):
    """KIO rulings (wyroki, postanowienia, uchwały) from the UZP search portal."""

    source_id = "kio"
    supports_search = True
    supports_fetch = True

    # ------------------------------------------------------------------ search
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live search (metadata + snippet only; `fetch` stores the full text).

        filters: case_number (-> Sign; non-KIO signatures return []), date_from/date_to (issue date),
        pzp_article (-> Art, dictionary format e.g. "art. 226 ust. 1 pkt 5"), subject_index (-> ThIdx),
        sort (rank|date_asc|date_desc), court_type (anything but NATIONAL_APPEAL_CHAMBER returns [])."""
        f = filters or {}
        if f.get("court_type") and str(f["court_type"]).upper() != COURT_TYPE:
            return []
        sig = None
        if f.get("case_number"):
            sig = normalize_signature(str(f["case_number"]))
            if not sig:
                return []  # not a KIO case number
        hits: list[RemoteHit] = []
        page = 1
        while len(hits) < limit and page <= MAX_SEARCH_PAGES:
            form = search_form(phrase=None if sig else (query or None), signature=sig,
                               date_from=f.get("date_from"), date_to=f.get("date_to"),
                               pzp_article=f.get("pzp_article"), subject_index=f.get("subject_index"),
                               page=page, sort=f.get("sort"))
            res = search_page(client, form)
            for it in res.items[: limit - len(hits)]:
                jtype = DOC_TYPES.get(fold(it.doc_type or ""), (it.doc_type or "").upper())
                label = DOC_TYPES_PL.get(jtype, it.doc_type or "")
                d = it.issue_date.isoformat() if it.issue_date else "data niepewna"
                hits.append(RemoteHit(
                    document_id=f"kio:{it.internal_id}", kind="judgment",
                    title=f"{it.organ or COURT_NAME}, {label}, {d}, {', '.join(it.case_numbers)}",
                    snippet=(it.snippet or "")[:800], original_url=details_url(it.internal_id),
                    metadata={"court": it.organ or COURT_NAME, "court_type": COURT_TYPE,
                              "case_numbers": it.case_numbers, "judgment_date": it.issue_date.isoformat()
                              if it.issue_date else None, "judgment_date_raw": it.issue_date_raw,
                              "judgment_type": jtype, "total": res.total, "source": SOURCE_ID}))
            if page >= res.pages or not res.items:
                break
            page += 1
        return hits

    # ------------------------------------------------------------------ fetch
    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        m = re.fullmatch(r"kio:(\d{1,10})", document_id)
        if not m:
            return None
        j, _ = fetch_ruling(store, client, m.group(1), force=force)
        return j.document_id

    # ------------------------------------------------------------------ default sample
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        """Small sample from catalog defaults: `queries = [{query, since, until, ...}]`, `max_total`."""
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        queries = d.get("queries") or []
        if not queries:
            res.warnings.append("no default scope in the catalog ([sources.kio.defaults]); nothing fetched")
            res.ok = True
            return res
        max_total = min(limit or d.get("max_total", 10), d.get("max_total", 10))
        fetched, reused, seen = 0, 0, set()
        for q in queries:
            try:
                page = search_page(client, search_form(phrase=q.get("query"), date_from=q.get("since"),
                                                       date_to=q.get("until"), pzp_article=q.get("pzp_article"),
                                                       sort=q.get("sort", "date_desc")))
            except Exception as e:  # noqa: BLE001 - other queries may still work
                res.errors.append(f"search {q}: {type(e).__name__}: {e}")
                continue
            for it in page.items:
                if len(seen) >= max_total:
                    break
                if it.internal_id in seen:
                    continue
                seen.add(it.internal_id)
                try:
                    j, now = fetch_ruling(store, client, it.internal_id, force=force)
                except NotFoundUpstream:
                    res.warnings.append(f"kio:{it.internal_id}: listed in search but not available upstream; not stored")
                    continue
                except Exception as e:  # noqa: BLE001 - one bad ruling must not stop the sample
                    res.errors.append(f"kio:{it.internal_id}: {type(e).__name__}: {e}")
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
        """Build from recorded pairs `kio/details_<id>.html` + `kio/content_<id>.html`."""
        res = SourceSyncResult(source_id=self.source_id)
        n = 0
        for p in sorted(_fixture_dir(fixtures).glob("details_*.html")):
            m = re.fullmatch(r"details_(\d+)\.html", p.name)
            if not m:
                continue
            c = p.with_name(f"content_{m.group(1)}.html")
            if not c.exists():
                continue
            try:
                j = ingest_ruling(store, m.group(1), p.read_bytes(), details_url(m.group(1)), mtime(p),
                                  c.read_bytes(), content_url(m.group(1)), mtime(c))
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"judgments": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    # ------------------------------------------------------------------ bulk
    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Paged import with checkpoint/resume.

        params: query (phrase), since/until (issue date, ISO), case_number, pzp_article, subject_index.
        Results are requested oldest first (`Srt=date_asc`) so earlier pages stay stable while new rulings
        are published. Cursor = next page number; a page is committed only when all its items were handled.
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
        sig = normalize_signature(str(params["case_number"])) if params.get("case_number") else None
        while True:
            if limits.limit is not None and stored_now >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            form = search_form(phrase=params.get("query"), signature=sig, date_from=params.get("since"),
                               date_to=params.get("until"), pzp_article=params.get("pzp_article"),
                               subject_index=params.get("subject_index"), page=page, sort="date_asc")
            try:
                result = search_page(client, form)
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
                    if limits.resume and store.get_judgment(f"kio:{it.internal_id}") is not None:
                        continue
                    try:
                        fetch_ruling(store, client, it.internal_id, force=not limits.resume)
                        stored_now += 1
                    except NotFoundUpstream:
                        res.warnings.append(f"kio:{it.internal_id}: listed in search but 404 upstream; not stored")
                    except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
                        res.errors.append(f"kio:{it.internal_id}: {type(e).__name__}: {e}")
                if page_complete:
                    items_total += len(result.items)
                    page += 1
                last = page > result.pages
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=page_complete and last,
                                     items=items_total, bytes_=store.data_size_bytes())
            if progress:
                progress(f"kio: next page {page}/{result.pages}, stored {stored_now} in this run")
            if not page_complete or last:
                break
        res.counts = {"stored": stored_now, "pages_done": page - 1}
        res.ok = not res.errors
        self.record(store, success=stored_now > 0 or res.ok, partial=bool(res.errors), offline=False)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {}).get("judgments", 0)
        return f"{n} KIO rulings stored locally (sample; UZP portal)", []
