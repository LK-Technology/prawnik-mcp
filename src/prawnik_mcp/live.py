"""Hybrid access: live search against source APIs and lazy fetching of missing documents.

- Live search fans out to connectors that support it, under a total time budget; sources that time
  out or fail are reported, never silently dropped. Responses are cached (TTL) in the local store.
- Lazy fetch stores a document (with snapshot) when `get_legal_document` is asked for an id that is
  not in the local corpus.
- `PRAWNIK_MCP_OFFLINE=1` disables all network access.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta

from prawnik_mcp.connectors import registry
from prawnik_mcp.connectors.base import RemoteHit
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.store import Store

SEARCH_TTL = timedelta(hours=24)
DEFAULT_BUDGET_S = 8.0

# Tests replace this with a factory returning a client over httpx.MockTransport.
CLIENT_FACTORY: Callable[[], PoliteClient] = PoliteClient


def live_enabled() -> bool:
    return os.environ.get("PRAWNIK_MCP_OFFLINE", "") not in ("1", "true", "yes")


def _cache_key(source_id: str, query: str, filters: dict, limit: int) -> str:
    raw = json.dumps([source_id, query, filters, limit], sort_keys=True, ensure_ascii=False)
    return "search:" + hashlib.sha256(raw.encode()).hexdigest()


def live_search(store: Store, query: str, *, kinds: set[str] | None, filters: dict | None, limit: int,
                budget_s: float = DEFAULT_BUDGET_S, source_ids: list[str] | None = None,
                ) -> tuple[list[tuple[str, RemoteHit, str]], list[str], list[str]]:
    """Returns ([(source_id, hit, origin 'live'|'cache')], warnings, unavailable_source_ids)."""
    filters = dict(filters or {})
    conns = [c for c in registry.all_connectors() if c.supports_search
             and (not kinds or set(c.info.kinds) & kinds) and (not source_ids or c.source_id in source_ids)]
    results: list[tuple[str, RemoteHit, str]] = []
    warnings: list[str] = []
    unavailable: list[str] = []
    todo = []
    for c in conns:
        cached = store.cache_get(_cache_key(c.source_id, query, filters, limit))
        if cached is not None:
            results += [(c.source_id, RemoteHit.model_validate(h), "cache") for h in json.loads(cached)]
        else:
            todo.append(c)
    if not todo:
        return results, warnings, unavailable
    client = CLIENT_FACTORY()
    pool = ThreadPoolExecutor(max_workers=len(todo))
    try:
        futures = {pool.submit(c.search, client, query, limit=limit, filters=filters): c for c in todo}
        done, pending = wait(futures, timeout=budget_s)
        for fut in pending:
            c = futures[fut]
            unavailable.append(c.source_id)
            warnings.append(f"{c.source_id}: brak odpowiedzi w limicie {budget_s:.0f} s — wynik niepełny.")
        for fut in done:
            c = futures[fut]
            try:
                hits = fut.result()
            except Exception as e:  # noqa: BLE001 - one failing source must not break the search
                unavailable.append(c.source_id)
                warnings.append(f"{c.source_id}: wyszukiwanie na żywo niedostępne ({type(e).__name__}).")
                continue
            store.cache_put(_cache_key(c.source_id, query, filters, limit),
                            json.dumps([h.model_dump(mode="json") for h in hits], ensure_ascii=False),
                            ttl=SEARCH_TTL, source_id=c.source_id)
            results += [(c.source_id, h, "live") for h in hits]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        client.close()
    return results, warnings, unavailable


def lazy_fetch(store: Store, document_id: str) -> tuple[bool, str | None]:
    """Fetch and store a document missing locally. Returns (stored, warning)."""
    conn = registry.for_document(document_id)
    if conn is None or not conn.supports_fetch:
        return False, None
    client = CLIENT_FACTORY()
    try:
        stored = conn.fetch(store, client, document_id)
        return stored is not None, None
    except NotFoundUpstream:
        return False, (f"{document_id}: źródło zwróciło 404 dla tego identyfikatora lub formatu/języka "
                       "(np. brak polskiej wersji XHTML starszego aktu UE). To nie dowodzi, że akt nie istnieje.")
    except Exception as e:  # noqa: BLE001
        return False, f"{document_id}: nie udało się pobrać ze źródła ({type(e).__name__}: {e})."
    finally:
        client.close()
