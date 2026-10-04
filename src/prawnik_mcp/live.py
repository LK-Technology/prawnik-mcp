"""Hybrid access: live search against source APIs and lazy fetching of missing documents.

- Live search fans out to connectors that support it, under a total time budget; sources that time
  out or fail are reported, never silently dropped. Responses are cached (TTL) in the local store.
  A source that answers after the budget still finishes in the background; its result is cached on the
  next call, so repeating the search a moment later picks it up.
- Lazy fetch stores a document (with snapshot) when `get_legal_document` is asked for an id that is
  not in the local corpus.
- `PRAWNIK_MCP_OFFLINE=1` disables all network access.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from prawnik_mcp.connectors import registry
from prawnik_mcp.connectors.base import RemoteHit
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.store import Store

SEARCH_TTL = timedelta(hours=24)
DEFAULT_BUDGET_S = 8.0

# Tests replace this with a factory returning a client over httpx.MockTransport.
CLIENT_FACTORY: Callable[[], PoliteClient] = PoliteClient

# Results that arrived after the time budget: cache key -> (source_id, JSON body). Written by worker
# threads, moved into the store by the next live_search call (the store is used from one thread).
_LATE: dict[str, tuple[str, str]] = {}
_RESOLVED: dict[str, str] = {}  # requested id -> id the source stored it under
_LATE_LOCK = threading.Lock()


def live_enabled() -> bool:
    return os.environ.get("PRAWNIK_MCP_OFFLINE", "") not in ("1", "true", "yes")


def _cache_key(source_id: str, query: str, filters: dict, limit: int) -> str:
    raw = json.dumps([source_id, query, filters, limit], sort_keys=True, ensure_ascii=False)
    return "search:" + hashlib.sha256(raw.encode()).hexdigest()


def live_search(store: Store, query: str, *, kinds: set[str] | None, filters: dict | None, limit: int,
                budget_s: float = DEFAULT_BUDGET_S, source_ids: list[str] | None = None,
                ) -> tuple[list[tuple[str, RemoteHit, str]], list[str], list[str], list[str]]:
    """Returns ([(source_id, hit, origin 'live'|'cache')], warnings, unavailable_source_ids, searched_source_ids)."""
    filters = dict(filters or {})
    _flush_late(store)
    conns = [c for c in registry.all_connectors() if c.supports_search
             and (not kinds or set(c.info.kinds) & kinds) and (not source_ids or c.source_id in source_ids)
             and (c.live_phrase_search or source_ids or filters.get("case_number") or filters.get("court_type"))]
    results: list[tuple[str, RemoteHit, str]] = []
    warnings: list[str] = []
    unavailable: list[str] = []
    searched: list[str] = []
    todo = []
    for c in conns:
        cached = store.cache_get(_cache_key(c.source_id, query, filters, limit))
        if cached is not None:
            results += [(c.source_id, RemoteHit.model_validate(h), "cache") for h in json.loads(cached)]
            searched.append(c.source_id)
        else:
            todo.append(c)
    if not todo:
        return results, warnings, unavailable, searched
    client = CLIENT_FACTORY()
    cond = threading.Condition()
    state: dict = {"left": len(todo), "closed": False, "out": {}}

    def worker(c) -> None:
        key = _cache_key(c.source_id, query, filters, limit)
        try:
            out: tuple = ("ok", c.search(client, query, limit=limit, filters=filters))
        except Exception as e:  # noqa: BLE001 - one failing source must not break the search
            out = ("error", e)
        with cond:
            state["left"] -= 1
            if state["closed"]:
                if out[0] == "ok":
                    with _LATE_LOCK:
                        _LATE[key] = (c.source_id, _dump(out[1]))
            else:
                state["out"][c.source_id] = out
            close_now = state["closed"] and state["left"] == 0
            cond.notify_all()
        if close_now:
            client.close()

    for c in todo:
        # daemon threads: a slow source never keeps the CLI process alive
        threading.Thread(target=worker, args=(c,), daemon=True, name=f"live-{c.source_id}").start()
    deadline = time.monotonic() + budget_s
    with cond:
        while state["left"] > 0 and (remaining := deadline - time.monotonic()) > 0:
            cond.wait(remaining)
        state["closed"] = True
        finished = dict(state["out"])
        all_done = state["left"] == 0
    if all_done:
        client.close()
    for c in todo:
        if c.source_id not in finished:
            unavailable.append(c.source_id)
            warnings.append(f"{c.source_id}: brak odpowiedzi w limicie {budget_s:.0f} s — wynik niepełny. "
                            "Zapytanie kończy się w tle; w działającym serwerze ponowienie wyszukiwania za chwilę użyje wyniku z cache.")
            continue
        kind, value = finished[c.source_id]
        if kind == "error":
            unavailable.append(c.source_id)
            detail = f": {value.reason}" if isinstance(value, SourceUnavailable) and value.reason else ""
            warnings.append(f"{c.source_id}: wyszukiwanie na żywo niedostępne ({type(value).__name__}{detail}).")
            continue
        store.cache_put(_cache_key(c.source_id, query, filters, limit), _dump(value),
                        ttl=SEARCH_TTL, source_id=c.source_id)
        results += [(c.source_id, h, "live") for h in value]
        searched.append(c.source_id)
    return results, warnings, unavailable, searched


def _dump(hits: list[RemoteHit]) -> str:
    return json.dumps([h.model_dump(mode="json") for h in hits], ensure_ascii=False)


def _flush_late(store: Store) -> None:
    with _LATE_LOCK:
        late = dict(_LATE)
        _LATE.clear()
    for key, (source_id, body) in late.items():
        store.cache_put(key, body, ttl=SEARCH_TTL, source_id=source_id)


def resolved_id(document_id: str) -> str:
    return _RESOLVED.get(document_id, document_id)


def interleave(results: list[tuple[str, RemoteHit, str]]) -> list[tuple[str, RemoteHit, str]]:
    """Round-robin over sources, keeping each source's own order, so one source cannot fill the page."""
    by_source: dict[str, list[tuple[str, RemoteHit, str]]] = {}
    for r in results:
        by_source.setdefault(r[0], []).append(r)
    out: list[tuple[str, RemoteHit, str]] = []
    queues = list(by_source.values())
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def lazy_fetch(store: Store, document_id: str) -> tuple[bool, str | None]:
    """Fetch and store a document missing locally. Returns (stored, warning). When the source stores the
    document under another id (an ELI consolidated-text notice resolves to its base act), the warning says so
    and `resolved_id(store, document_id)` gives the id to use."""
    conn = registry.for_document(document_id)
    if conn is None or not conn.supports_fetch:
        return False, None
    client = CLIENT_FACTORY()
    try:
        stored = conn.fetch(store, client, document_id)
        if stored and stored != document_id:
            _RESOLVED[document_id] = stored
            return True, (f"{document_id} to obwieszczenie o tekście jednolitym aktu {stored}; zwracam przepis "
                          f"z aktu podstawowego w najnowszym tekście jednolitym. Cytuj {stored}.")
        return stored is not None, None
    except NotFoundUpstream:
        return False, (f"{document_id}: źródło zwróciło 404 dla tego identyfikatora lub formatu/języka "
                       "(np. brak polskiej wersji XHTML starszego aktu UE). To nie dowodzi, że akt nie istnieje.")
    except Exception as e:  # noqa: BLE001
        return False, f"{document_id}: nie udało się pobrać ze źródła ({type(e).__name__}: {e})."
    finally:
        client.close()
