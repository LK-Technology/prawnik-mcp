"""EUREKA (eureka.mf.gov.pl) connector: Polish tax interpretations of the Ministry of Finance / KIS.

Keyless public JSON API behind the EUREKA web app (endpoints first documented by
matematicsolutions/mcp-eureka, MIT; re-verified live on 2026-09-28):

- search:   POST /api/public/v1/wyszukiwarka/informacje/?size=N&page=N&sort=... (trailing slash required;
            dictionary filters are arrays of numeric ids; `searchQuery` must be omitted when empty);
- document: GET  /api/public/v1/informacje/{id} (404 with a JSON error body for unknown ids).

Search results carry only signature, date, thesis and labels; every document is fetched in full
before it is stored. Sorting by issue date alone is not stable across pages (a live probe returned
the same id on two pages and skipped another), so every query adds `ID_INFORMACJI` as a tie-breaker,
and bulk sync de-duplicates ids and compares the number seen with `totalHits`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.eureka import (
    API,
    PARSER_VERSION,
    EurekaDataError,
    detail_url,
    parse_eureka_detail,
    parse_search_results,
    portal_url,
)
from prawnik_mcp.store import Store

SOURCE_ID = "eureka"
ACCEPT = "application/json"
SEARCH_URL = f"{API}/wyszukiwarka/informacje/"  # the trailing slash is mandatory (HTTP 500 without it)
SEARCH_COLUMNS = ["ID_INFORMACJI", "KATEGORIA_INFORMACJI", "SYG", "DT_WYD", "TEZA", "STATUS_INFORMACJI"]
DEFAULT_CATEGORY_IDS = [1]  # "Interpretacja indywidualna" (verified: detail of a category-1 hit)
MAX_PAGE_SIZE = 50
# New-style KIS signature, e.g. 0115-KDIT3.4011.582.2026.2.AWO; a query that is only a signature uses the SYG filter.
SIGNATURE_RE = re.compile(r"\d{4}-[A-Z][A-Z0-9-]*\.\d{3,4}\.\d+\.\d{4}\.\d+\.[A-Z0-9]+")


def search_body(*, query: str | None = None, signature: str | None = None, category_ids: list[int] | None = None,
                date_from: str | None = None, date_to: str | None = None, full_phrase: bool = False) -> dict[str, Any]:
    filt: dict[str, Any] = {}
    if signature:
        filt["SYG"] = signature
    if category_ids:
        filt["KATEGORIA_INFORMACJI"] = [int(c) for c in category_ids]
    if date_from:
        filt["DT_WYD_start"] = str(date_from)
    if date_to:
        filt["DT_WYD_end"] = str(date_to)
    body: dict[str, Any] = {
        "filter": filt, "columns": SEARCH_COLUMNS, "searchInFullPhrase": bool(full_phrase),
        "searchInContent": False, "searchInSynonyms": False, "warunkiDodatkowe": [],
    }
    if query and query.strip():
        body["searchQuery"] = query.strip()  # never send null/empty (HTTP 500)
    return body


def search_url(*, page: int, size: int) -> str:
    size = max(1, min(int(size), MAX_PAGE_SIZE))
    return f"{SEARCH_URL}?size={size}&page={max(0, int(page))}&sort=DT_WYD%2Cdesc&sort=ID_INFORMACJI%2Cdesc"


def _category_ids(value: Any) -> list[int]:
    if value is None:
        return list(DEFAULT_CATEGORY_IDS)
    vals = value if isinstance(value, list | tuple) else [value]
    return [int(v) for v in vals]


def search_page(client: PoliteClient, body: dict[str, Any], *, page: int, size: int
                ) -> tuple[list[dict[str, Any]], int | None, datetime]:
    r = client.post(search_url(page=page, size=size), json=body, accept=ACCEPT)
    rows, total = parse_search_results(r.content)
    return rows, total, r.fetched_at


def ingest_detail_bytes(store: Store, content: bytes, url: str, fetched_at: datetime | None = None, *,
                        expected_id: str | None = None) -> Judgment:
    """Snapshot first (raw bytes are kept even if parsing fails), then parse and index."""
    snap = store.put_snapshot(SOURCE_ID, url, content, "application/json",
                              parser_version=PARSER_VERSION, fetched_at=fetched_at)
    judgment, doc = parse_eureka_detail(content, snap.snapshot_id, snap.sha256, snap.fetched_at)
    if expected_id is not None and judgment.source_judgment_id != str(expected_id):
        raise EurekaDataError(f"requested id {expected_id}, response holds id {judgment.source_judgment_id} "
                              f"(snapshot {snap.snapshot_id} kept, not indexed)")
    with store.batch():
        store.upsert_document(doc)
        store.upsert_judgment(judgment, title=doc.title)
    return judgment


def fetch_detail(store: Store, client: PoliteClient, eureka_id: str, *, force: bool = False) -> tuple[Judgment, bool]:
    """Returns (record, fetched_now). An already stored snapshot of the URL is re-parsed, not re-fetched."""
    url = detail_url(eureka_id)
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            return ingest_detail_bytes(store, content, url, snap.fetched_at, expected_id=str(eureka_id)), False
    r = client.get(url, accept=ACCEPT)  # NotFoundUpstream on 404: nothing is stored
    return ingest_detail_bytes(store, r.content, url, r.fetched_at, expected_id=str(eureka_id)), True


@dataclass
class EurekaIngest:
    stored: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- connector


class EurekaConnector(BaseConnector):
    """Tax interpretations and other MF/KIS tax information from EUREKA."""

    source_id = SOURCE_ID
    supports_search = True
    supports_fetch = True

    # ------------------------------------------------------------------ live search
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live search (signature, thesis and metadata; not the full text). Filters: signature,
        category_ids (default [1] = individual interpretations), date_from/date_to, full_phrase."""
        f = filters or {}
        q = (query or "").strip()
        signature = f.get("signature")
        if not signature and SIGNATURE_RE.fullmatch(q):
            signature, q = q, ""
        body = search_body(query=q or None, signature=signature, category_ids=_category_ids(f.get("category_ids")),
                           date_from=f.get("date_from"), date_to=f.get("date_to"),
                           full_phrase=bool(f.get("full_phrase")))
        rows, total, _ = search_page(client, body, page=0, size=max(limit, 1))
        hits = []
        for it in rows[:limit]:
            title = ", ".join(x for x in (it["category"] or "EUREKA", it["signature"] or "(brak sygnatury)",
                                          it["issue_date"]) if x)
            hits.append(RemoteHit(
                document_id=f"eureka:{it['id']}", kind="tax_ruling", title=title,
                snippet=it["thesis"][:800], original_url=portal_url(it["id"]),
                metadata={"signature": it["signature"], "issue_date": it["issue_date"], "category": it["category"],
                          "status": it["status"], "eureka_id": it["id"], "total_hits": total, "source": SOURCE_ID,
                          "note": "Teza z listy wyników; pełna treść przez get_legal_document. "
                                  "Interpretacja nie jest źródłem prawa."}))
        return hits

    # ------------------------------------------------------------------ fetch
    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        m = re.fullmatch(r"eureka:(\d+)", document_id)
        if not m:
            return None
        j, _ = fetch_detail(store, client, m.group(1), force=force)
        return j.document_id

    # ------------------------------------------------------------------ default sample
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        cap = int(d.get("max_total", 10))
        max_total = min(limit or cap, cap)
        queries = d.get("queries") or []
        if not queries:
            res.warnings.append("no default scope in the catalog ([sources.eureka.defaults].queries)")
        ing = EurekaIngest()
        seen: set[str] = set()
        for q in queries:
            if len(seen) >= max_total:
                break
            body = search_body(query=q.get("query"), signature=q.get("signature"),
                               category_ids=_category_ids(q.get("category_ids")),
                               date_from=q.get("since"), date_to=q.get("until"))
            try:
                rows, _, _ = search_page(client, body, page=0, size=min(max_total - len(seen), MAX_PAGE_SIZE))
            except Exception as e:  # noqa: BLE001 - other queries may still work
                ing.errors.append(f"wyszukiwanie {q.get('query') or q}: {type(e).__name__}: {e}")
                continue
            for it in rows:
                if it["id"] in seen or len(seen) >= max_total:
                    continue
                seen.add(it["id"])
                self._fetch_one(store, client, it["id"], force, ing)
        res.counts = {"fetched": len(ing.stored), "reused_checkpoint": len(ing.reused)}
        res.errors, res.warnings = ing.errors, res.warnings + ing.warnings
        success = bool(ing.stored or ing.reused)
        res.ok = success and not ing.errors
        self.record(store, success=success, partial=bool(ing.errors), offline=False)
        return res

    def _fetch_one(self, store: Store, client: PoliteClient, eid: str, force: bool, ing: EurekaIngest) -> bool:
        try:
            j, fetched = fetch_detail(store, client, eid, force=force)
        except NotFoundUpstream:
            ing.warnings.append(f"eureka:{eid}: listed by search but the document returns 404 (not stored)")
            return False
        except Exception as e:  # noqa: BLE001 - one bad document must not stop the batch
            ing.errors.append(f"eureka:{eid}: {type(e).__name__}: {e}")
            return False
        (ing.stored if fetched else ing.reused).append(j.document_id)
        if j.data_quality_flags:
            ing.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
        return True

    # ------------------------------------------------------------------ offline
    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        """Builds the corpus from recorded `informacje_<id>.json` responses (dir `<fixtures>/eureka`)."""
        res = SourceSyncResult(source_id=self.source_id)
        base = fixtures / "eureka" if (fixtures / "eureka").is_dir() else fixtures
        n = 0
        for p in sorted(base.glob("informacje_*.json")):
            m = re.fullmatch(r"informacje_(\d+)\.json", p.name)
            if not m:
                continue  # e.g. informacje_<id>.404.json (recorded error body)
            try:
                j = ingest_detail_bytes(store, p.read_bytes(), detail_url(m.group(1)), mtime(p),
                                        expected_id=m.group(1))
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"documents": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    # ------------------------------------------------------------------ bulk
    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Search-driven bulk import with checkpoint/resume (cursor = next page number).

        params: query, signature, category_ids (default [1]), since/until (issue date, YYYY-MM-DD),
        page_size (1..50, default 50). Each listed id is fetched in full before it is stored.
        """
        res = SourceSyncResult(source_id=self.source_id)
        scope = scope_key(self.source_id, {"mode": "search", **params})
        state = store.get_sync_state(self.source_id, scope) if limits.resume else None
        if state and state["done"]:
            res.ok = True
            res.warnings.append(f"scope {scope} already complete ({state['items']} items); use --no-resume to redo")
            return res
        size = max(1, min(int(params.get("page_size") or MAX_PAGE_SIZE), MAX_PAGE_SIZE))
        body = search_body(query=params.get("query"), signature=params.get("signature"),
                           category_ids=_category_ids(params.get("category_ids")),
                           date_from=params.get("since"), date_to=params.get("until"))
        start_page = page = int(state["cursor"]) if state and state["cursor"] else 0
        items_total = state["items"] if state else 0
        ing = EurekaIngest()
        seen: set[str] = set()
        duplicates = 0
        total: int | None = None
        done = False

        def checkpoint(is_done: bool) -> None:
            store.set_sync_state(self.source_id, scope, cursor=str(page), done=is_done, items=items_total,
                                 bytes_=store.data_size_bytes())

        while True:
            if limits.limit is not None and len(ing.stored) >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            try:
                rows, total, _ = search_page(client, body, page=page, size=size)
            except Exception as e:  # noqa: BLE001 - keep checkpoint, report
                res.errors.append(f"page {page}: {type(e).__name__}: {e}")
                break
            if not rows:
                done = True
                checkpoint(True)
                break
            page_complete = True
            for it in rows:
                if limits.limit is not None and len(ing.stored) >= limits.limit:
                    page_complete = False  # resume must revisit the rest of this page
                    break
                if it["id"] in seen:
                    duplicates += 1
                    continue
                seen.add(it["id"])
                if limits.resume and store.get_document(f"eureka:{it['id']}") is not None:
                    continue  # stored earlier (previous run or overlapping scope)
                self._fetch_one(store, client, it["id"], not limits.resume, ing)
            if page_complete:
                page += 1
                items_total += len(rows)
            last = page_complete and (len(rows) < size or (total is not None and page * size >= total))
            checkpoint(last)
            if progress:
                progress(f"eureka: page {page}, stored {len(ing.stored)} in this run")
            if not page_complete:
                break
            if last:
                done = True
                break

        if duplicates:
            res.warnings.append(f"search listing repeated {duplicates} id(s) across pages (unstable upstream "
                                "pagination); documents may have been skipped — re-run with a narrower date window")
        if done and start_page == 0 and total is not None and len(seen) < total:
            res.warnings.append(f"listing yielded {len(seen)} distinct ids but totalHits={total}; "
                                "the scope may be incomplete")
        res.errors += ing.errors
        res.warnings += ing.warnings
        res.counts = {"stored": len(ing.stored), "reused_checkpoint": len(ing.reused), "pages_done": page}
        if total is not None:
            res.counts["total_hits"] = total
        res.ok = not res.errors
        self.record(store, success=bool(ing.stored) or res.ok, partial=bool(res.errors), offline=False)
        return res

    # ------------------------------------------------------------------ coverage
    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(SOURCE_ID, {}).get("documents", 0)
        return f"{n} EUREKA tax documents (mostly individual interpretations) stored locally", []
