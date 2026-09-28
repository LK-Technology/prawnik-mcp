"""Corpus synchronisation across all implemented connectors.

`sync_corpus(store)` fetches each source's default scope from the catalog (small by design);
`sync_corpus(store, offline_fixtures=Path(...))` builds the same corpus from recorded samples without
network. A failure of one source never stops the others; the source is then marked `unavailable`
and its previously stored data are kept.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from prawnik_mcp.connectors import registry
from prawnik_mcp.connectors.base import SourceSyncResult
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.store import Store

__all__ = ["SourceSyncResult", "SyncReport", "sync_corpus"]

SAOS_MAX_TOTAL = 30  # kept for backwards compatibility; the catalog holds the default


class SyncReport(BaseModel):
    mode: Literal["online", "offline_fixtures"]
    started_at: datetime
    finished_at: datetime | None = None
    sources: dict[str, SourceSyncResult] = Field(default_factory=dict)
    store_stats: dict[str, int] = {}

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.sources.values())


def sync_corpus(store: Store, client: PoliteClient | None = None, *, offline_fixtures: Path | None = None,
                force: bool = False, saos_max: int | None = None, only: list[str] | None = None) -> SyncReport:
    report = SyncReport(mode="offline_fixtures" if offline_fixtures else "online", started_at=datetime.now(UTC))
    connectors = [c for c in registry.all_connectors() if not only or c.source_id in only]
    if offline_fixtures is not None:
        for c in connectors:
            report.sources[c.source_id] = c.sync_offline(store, Path(offline_fixtures))
    else:
        own = client is None
        client = client or PoliteClient()
        try:
            for c in connectors:
                limit = saos_max if c.source_id == "saos" else None
                report.sources[c.source_id] = c.sync_defaults(store, client, force=force, limit=limit)
        finally:
            if own:
                client.close()
    report.finished_at = datetime.now(UTC)
    report.store_stats = store.stats()
    return report
