"""On-demand lookups in public entity registries: KRS (MS), VAT white list (MF) and VIES (EC).

Nothing here is synced or indexed as a legal document. `lookup.lookup_entity` builds an entity card
from live answers, with per-source provenance and a typed status per source:

- ok: the registry answered with data;
- not_found: the registry answered "no such entity" (only then);
- invalid_input: the identifier failed local validation (no request was made);
- source_unavailable: network/HTTP/format failure; says nothing about the entity;
- daily_quota_exhausted: the local guard of the white-list quota refused the call (nothing was sent),
  or the upstream refused it for quota reasons;
- skipped: not queried (e.g. no KRS number to follow, or not requested).

Privacy rules enforced in code: no search by name, surname or PESEL; KRS names are passed on as masked
by MS and every PESEL-like number is removed from free text; white-list answers about natural persons
are reduced to a minimal card and bank accounts are never listed; VIES answers are never stored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SourceStatus(str, Enum):
    ok = "ok"
    not_found = "not_found"
    invalid_input = "invalid_input"
    source_unavailable = "source_unavailable"
    daily_quota_exhausted = "daily_quota_exhausted"
    skipped = "skipped"


@dataclass
class SourceOutcome:
    """Provenance and status of one registry answer (one entry of `data.sources` in the entity card)."""

    source_id: str  # catalog id: krs | wl_vat | vies
    status: SourceStatus
    url: str | None = None  # URL actually fetched (account numbers masked)
    fetched_at: datetime | None = None
    state_as_of: str | None = None  # date the registry states its data for (KRS stanZDnia, white-list date); never guessed
    snapshot_id: str | None = None  # local raw snapshot (None for VIES: never stored)
    stored: bool = False
    processing: str | None = None  # what was done to the answer (open-data reuse condition)
    attribution: str | None = None
    detail: str | None = None  # reason for a non-ok status, Polish
    requests: int = 0  # HTTP requests made for this source in this lookup
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        extra = d.pop("extra") or {}
        d.update(extra)
        return d


__all__ = ["SourceOutcome", "SourceStatus"]
