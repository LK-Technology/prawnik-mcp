"""SAOS (www.saos.org.pl) connector: small topical samples of judgments via the public API.

Search results contain only snippets; every judgment is fetched in full from
`/api/judgments/{id}` before it is stored. No bulk dump is used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

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
