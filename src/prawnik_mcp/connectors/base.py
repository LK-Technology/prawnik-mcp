"""Common connector interface, sync result models and the source-record helper.

A connector turns one legal data source into stored `LegalDocument` / `ProvisionVersion` /
`Judgment` records with snapshots. Live search and single-document fetch are optional
capabilities (see `supports_search` / `supports_fetch`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from prawnik_mcp import sources
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.contracts import SourceRecord
from prawnik_mcp.store import Store


class SourceSyncResult(BaseModel):
    source_id: str
    ok: bool = False
    counts: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []


class RemoteHit(BaseModel):
    """A search result straight from a source API (not yet stored locally)."""

    document_id: str  # id usable with get_legal_document (fetched lazily)
    kind: str
    title: str
    snippet: str = ""
    original_url: str
    metadata: dict[str, Any] = {}


@runtime_checkable
class Connector(Protocol):
    source_id: str

    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult: ...

    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult: ...

    def coverage(self, store: Store) -> tuple[str, list[str]]: ...


class BaseConnector:
    source_id: ClassVar[str]
    supports_search: ClassVar[bool] = False
    supports_fetch: ClassVar[bool] = False

    @property
    def info(self) -> sources.SourceInfo:
        return sources.source(self.source_id)

    # Optional capabilities -------------------------------------------------------------
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict[str, Any] | None = None) -> list[RemoteHit]:
        raise NotImplementedError

    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        """Fetch one document by id and store it. Returns the stored document id, or None if not handled."""
        raise NotImplementedError

    # Source record ---------------------------------------------------------------------
    def record(self, store: Store, *, success: bool, partial: bool, offline: bool) -> SourceRecord:
        info = self.info
        prev = store.get_source(self.source_id)
        coverage, intervals = self.coverage(store)
        if success:
            status = "degraded" if (partial or offline) else "ok"
            last = prev.last_successful_sync if (prev and offline) else (None if offline else datetime.now(UTC))
            cov = coverage + (" [built from recorded offline samples]" if offline else "")
            ivs = intervals
        else:
            status = "unavailable"
            last = prev.last_successful_sync if prev else None
            cov = prev.coverage if prev else "no local data"
            ivs = prev.supported_intervals if prev else []
        rec = SourceRecord(
            source_id=self.source_id, name=info.name, maturity=info.maturity, terms_url=info.terms_url,
            publisher=info.publisher, base_url=info.base_url, terms_of_use=info.terms,
            terms_checked_at=info.terms_checked_at, required_attribution=info.attribution,
            last_successful_sync=last, access_status=status, coverage=cov,
            known_gaps=list(info.known_gaps), supported_intervals=ivs,
        )
        store.upsert_source(rec)
        return rec

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {})
        return (", ".join(f"{k}: {v}" for k, v in n.items()) or "no local data"), []


def mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC)
