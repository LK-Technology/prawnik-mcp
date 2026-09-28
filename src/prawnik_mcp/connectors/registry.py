"""Registry of implemented connectors, keyed by catalog source id."""

from __future__ import annotations

from functools import lru_cache

from prawnik_mcp.connectors.base import BaseConnector


@lru_cache(maxsize=1)
def _registry() -> dict[str, BaseConnector]:
    from prawnik_mcp.connectors.cbosa import CbosaConnector
    from prawnik_mcp.connectors.cellar import CellarConnector
    from prawnik_mcp.connectors.eli import EliConnector
    from prawnik_mcp.connectors.eureka import EurekaConnector
    from prawnik_mcp.connectors.kio import KioConnector
    from prawnik_mcp.connectors.saos import SaosConnector
    from prawnik_mcp.connectors.uodo import UodoConnector

    conns: list[BaseConnector] = [EliConnector(), CellarConnector(), SaosConnector(), EurekaConnector(),
                                  KioConnector(), UodoConnector(), CbosaConnector()]
    return {c.source_id: c for c in conns}


def get(source_id: str) -> BaseConnector:
    try:
        return _registry()[source_id]
    except KeyError:
        raise KeyError(f"no connector implemented for source {source_id!r}") from None


def all_connectors() -> list[BaseConnector]:
    return list(_registry().values())


def for_document(document_id: str) -> BaseConnector | None:
    from prawnik_mcp import sources

    sid = sources.source_for_document(document_id)
    return _registry().get(sid) if sid else None
