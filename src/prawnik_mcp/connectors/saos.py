"""SAOS (www.saos.org.pl) connector: small topical samples of judgments via the public API.

Search results contain only snippets; every judgment is fetched in full from
`/api/judgments/{id}` before it is stored. No bulk dump is used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.saos import PARSER_VERSION, parse_saos_judgment
from prawnik_mcp.store import Store

SAOS_API = "https://www.saos.org.pl/api"
SOURCE_ID = "saos"
ACCEPT = "application/json"


def judgment_url(saos_id: int | str) -> str:
    return f"{SAOS_API}/judgments/{saos_id}"


def search_url(query: dict[str, str] | str, *, page_size: int = 10) -> str:
    """`query` is a dict of SAOS search parameters (all, keywords, courtType, judgmentDateFrom, ...)
    or a plain full-text string. Results are sorted by judgment date, newest first."""
    params: dict[str, str | int] = {"all": query} if isinstance(query, str) else dict(query)
    params.setdefault("sortingField", "JUDGMENT_DATE")
    params.setdefault("sortingDirection", "DESC")
    params.update(pageSize=page_size, pageNumber=0)
    return f"{SAOS_API}/search/judgments?{urlencode(params)}"


@dataclass
class SaosIngest:
    judgments: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    flags: dict[str, list[str]] = field(default_factory=dict)


def ingest_judgment_bytes(store: Store, content: bytes, url: str, fetched_at: datetime | None = None) -> Judgment:
    snap = store.put_snapshot(SOURCE_ID, url, content, "application/json",
                              parser_version=PARSER_VERSION, fetched_at=fetched_at)
    judgment, doc = parse_saos_judgment(content, snap.snapshot_id, snap.sha256, snap.fetched_at)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


def search_ids(client: PoliteClient, query: dict[str, str] | str, *, limit: int) -> list[int]:
    r = client.get(search_url(query, page_size=max(limit, 1)), accept=ACCEPT)
    items = json.loads(r.content).get("items") or []
    return [int(i["id"]) for i in items[:limit] if "id" in i]


def fetch_judgment(store: Store, client: PoliteClient, saos_id: int, *, force: bool = False) -> tuple[Judgment, bool]:
    """Returns (judgment, fetched_now). Checkpoint: an already stored URL is re-parsed, not re-fetched."""
    url = judgment_url(saos_id)
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            judgment, doc = parse_saos_judgment(content, snap.snapshot_id, snap.sha256, snap.fetched_at)
            store.upsert_document(doc)
            store.upsert_judgment(judgment, title=doc.title)
            return judgment, False
    r = client.get(url, accept=ACCEPT)
    return ingest_judgment_bytes(store, r.content, r.url, r.fetched_at), True


def sync_queries(store: Store, client: PoliteClient, queries: list[dict[str, str]], *, max_total: int = 30,
                 per_query: int = 10, force: bool = False) -> SaosIngest:
    out = SaosIngest()
    seen: set[int] = set()
    for q in queries:
        if len(seen) >= max_total:
            break
        try:
            ids = search_ids(client, q, limit=min(per_query, max_total - len(seen)))
        except Exception as e:  # noqa: BLE001 - other queries may still work
            out.errors.append(f"wyszukiwanie {q.get('all', q) if isinstance(q, dict) else q}: {e}")
            continue
        for sid in ids:
            if sid in seen or len(seen) >= max_total:
                continue
            seen.add(sid)
            try:
                j, fetched = fetch_judgment(store, client, sid, force=force)
            except Exception as e:  # one bad judgment must not stop the sample
                out.errors.append(f"saos:{sid}: {e}")
                continue
            (out.judgments if fetched else out.skipped).append(j.document_id)
            if j.data_quality_flags:
                out.flags[j.document_id] = j.data_quality_flags
    return out


# --------------------------------------------------------------------------- connector


class SaosConnector(BaseConnector):
    """Polish court judgments (common courts, SN, TK, administrative, KIO) via the SAOS API."""

    source_id = "saos"
    supports_fetch = True

    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        max_total = min(limit or d.get("max_total", 30), d.get("max_total", 30))
        today = datetime.now(UTC).date().isoformat()
        queries = [{**q, "judgmentDateTo": today} for q in d.get("queries", [])]
        success = False
        try:
            ing = sync_queries(store, client, queries, max_total=max_total, force=force)
            res.counts = {"fetched": len(ing.judgments), "reused_checkpoint": len(ing.skipped)}
            res.errors = ing.errors
            res.warnings = [f"{k}: {', '.join(v)}" for k, v in ing.flags.items()]
            success = bool(ing.judgments or ing.skipped)
            res.ok = not ing.errors and success
        except Exception as e:  # noqa: BLE001
            res.errors.append(str(e))
        self.record(store, success=success, partial=bool(res.errors), offline=False)
        return res

    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        n = 0
        for p in sorted(fixtures.glob("saos_*.json")):
            if not re.fullmatch(r"saos_\d+\.json", p.name):
                continue
            try:
                j = ingest_judgment_bytes(store, p.read_bytes(), judgment_url(p.stem.split("_")[1]), mtime(p))
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"judgments": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    supports_search = True
    COURT_TYPES = ("COMMON", "SUPREME", "ADMINISTRATIVE", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER")

    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live full-text / case-number search. Snippets only; get_legal_document fetches the full text."""
        f = filters or {}
        params: dict[str, str] = {}
        if f.get("case_number"):
            params["caseNumber"] = f["case_number"]
        else:
            params["all"] = query
        if f.get("court_type"):
            params["courtType"] = str(f["court_type"]).upper()
        if f.get("date_from"):
            params["judgmentDateFrom"] = str(f["date_from"])
        if f.get("date_to"):
            params["judgmentDateTo"] = str(f["date_to"])
        r = client.get(search_url(params, page_size=max(10, limit)), accept=ACCEPT)
        hits = []
        for it in (json.loads(r.content).get("items") or [])[:limit]:
            div = it.get("division") or {}
            court = (div.get("court") or {}).get("name") or {
                "SUPREME": "Sąd Najwyższy", "CONSTITUTIONAL_TRIBUNAL": "Trybunał Konstytucyjny",
                "NATIONAL_APPEAL_CHAMBER": "Krajowa Izba Odwoławcza"}.get(it.get("courtType"), it.get("courtType", ""))
            cases = [c.get("caseNumber") for c in it.get("courtCases") or []]
            snippet = re.sub(r"<[^>]+>", "", it.get("textContent") or "").strip()
            hits.append(RemoteHit(
                document_id=f"saos:{it['id']}", kind="judgment",
                title=f"{court}, {it.get('judgmentType', '')}, {it.get('judgmentDate', '')}, {', '.join(cases)}",
                snippet=snippet[:800], original_url=judgment_url(it["id"]),
                metadata={"court": court, "court_type": it.get("courtType"), "case_numbers": cases,
                          "judgment_date": it.get("judgmentDate"), "source": "saos"}))
        return hits

    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Bulk import with checkpoint/resume.

        - `params["query"]` set: search API (all=query, courtType, dates) + full fetch of each judgment;
        - otherwise: dump API (full records, 100 per page) for a judgment-date window, optionally
          filtered locally by court type.
        """
        res = SourceSyncResult(source_id=self.source_id)
        mode = "search" if params.get("query") else "dump"
        scope = scope_key(self.source_id, {"mode": mode, **params})
        state = store.get_sync_state(self.source_id, scope) if limits.resume else None
        if state and state["done"]:
            res.ok = True
            res.warnings.append(f"scope {scope} already complete ({state['items']} items); use --no-resume to redo")
            return res
        page = int(state["cursor"]) if state and state["cursor"] else 0
        items_total = state["items"] if state else 0
        stored_now = 0
        court_type = (params.get("court_type") or "").upper() or None
        today = datetime.now(UTC).date().isoformat()
        while True:
            if limits.limit is not None and stored_now >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            if mode == "dump":
                q = {"pageSize": 100, "pageNumber": page, "judgmentStartDate": params.get("since") or "1900-01-01",
                     "judgmentEndDate": params.get("until") or today}
                url = f"{SAOS_API}/dump/judgments?{urlencode(q)}"
            else:
                q = {"all": params["query"], "pageSize": 100, "pageNumber": page,
                     "sortingField": "JUDGMENT_DATE", "sortingDirection": "DESC"}
                if court_type:
                    q["courtType"] = court_type
                if params.get("since"):
                    q["judgmentDateFrom"] = params["since"]
                if params.get("until"):
                    q["judgmentDateTo"] = params["until"]
                url = f"{SAOS_API}/search/judgments?{urlencode(q)}"
            try:
                r = client.get(url, accept=ACCEPT)
            except Exception as e:  # noqa: BLE001 - keep checkpoint, report
                res.errors.append(f"page {page}: {e}")
                break
            payload = json.loads(r.content)
            items = payload.get("items") or []
            if not items:
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=True, items=items_total,
                                     bytes_=store.data_size_bytes())
                break
            page_complete = True
            with store.batch():
                for it in items:
                    if limits.limit is not None and stored_now >= limits.limit:
                        page_complete = False  # resume must revisit the rest of this page
                        break
                    if court_type and it.get("courtType") != court_type:
                        continue
                    if store.get_judgment(f"saos:{it.get('id')}") is not None and limits.resume:
                        continue  # already stored (earlier run or overlapping scope)
                    try:
                        if mode == "dump":
                            body = json.dumps({"data": it}, ensure_ascii=False).encode("utf-8")
                            ingest_judgment_bytes(store, body, judgment_url(it["id"]), r.fetched_at)
                        else:
                            fetch_judgment(store, client, int(it["id"]), force=not limits.resume)
                        stored_now += 1
                    except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
                        res.errors.append(f"saos:{it.get('id')}: {type(e).__name__}: {e}")
                if page_complete:
                    page += 1
                    items_total += len(items)
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=False, items=items_total,
                                     bytes_=store.data_size_bytes())
            if not page_complete:
                break
            if progress:
                progress(f"saos {mode}: page {page}, stored {stored_now} in this run")
            if not any(link.get("rel") == "next" for link in payload.get("links") or []):
                store.set_sync_state(self.source_id, scope, cursor=str(page), done=True, items=items_total,
                                     bytes_=store.data_size_bytes())
                break
        res.counts = {"stored": stored_now, "pages_done": page}
        res.ok = not res.errors
        self.record(store, success=stored_now > 0 or res.ok, partial=bool(res.errors), offline=False)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get("saos", {}).get("judgments", 0)
        return f"{n} SAOS judgments stored locally", []

    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        m = re.fullmatch(r"saos:(\d+)", document_id)
        if not m:
            return None
        j, _ = fetch_judgment(store, client, int(m.group(1)), force=force)
        return j.document_id
