"""Declarative catalog of legal data sources (`catalog.toml`).

The catalog is the single place for per-source metadata: publisher, hosts, polite rate limits,
terms of reuse, attribution, maturity, known gaps, default sync scope and identifier aliases.
Connectors, the HTTP allowlist, `sources_status` and act aliases are all derived from it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

Maturity = Literal["stable", "beta", "experimental", "research"]


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    publisher: str
    base_url: str
    hosts: tuple[str, ...]
    rate_per_s: float
    kinds: tuple[str, ...]
    id_prefix: str
    maturity: Maturity
    terms: str
    terms_url: str
    terms_checked_at: date
    attribution: str
    known_gaps: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def implemented(self) -> bool:
        return self.maturity != "research"

    @property
    def min_delay(self) -> float:
        return 1.0 / self.rate_per_s if self.rate_per_s > 0 else 1.0


@lru_cache(maxsize=1)
def catalog() -> dict[str, SourceInfo]:
    raw = tomllib.loads(files("prawnik_mcp.sources").joinpath("catalog.toml").read_text(encoding="utf-8"))
    out: dict[str, SourceInfo] = {}
    for sid, s in raw["sources"].items():
        out[sid] = SourceInfo(
            source_id=sid, name=s["name"], publisher=s["publisher"], base_url=s["base_url"],
            hosts=tuple(s["hosts"]), rate_per_s=float(s["rate_per_s"]), kinds=tuple(s["kinds"]),
            id_prefix=s["id_prefix"], maturity=s["maturity"], terms=s["terms"], terms_url=s["terms_url"],
            terms_checked_at=date.fromisoformat(s["terms_checked_at"]), attribution=s["attribution"],
            known_gaps=tuple(s.get("known_gaps", ())), defaults=dict(s.get("defaults", {})),
            aliases={k.lower(): v for k, v in s.get("aliases", {}).items()},
        )
    return out


def source(source_id: str) -> SourceInfo:
    return catalog()[source_id]


def implemented_sources() -> list[SourceInfo]:
    return [s for s in catalog().values() if s.implemented]


def allowed_hosts() -> frozenset[str]:
    """Hosts the HTTP client may contact: only sources that are actually implemented."""
    return frozenset(h.lower() for s in implemented_sources() for h in s.hosts)


def host_delays() -> dict[str, float]:
    return {h.lower(): s.min_delay for s in implemented_sources() for h in s.hosts}


def source_for_document(document_id: str) -> str | None:
    for s in catalog().values():
        if document_id.startswith(s.id_prefix):
            return s.source_id
    return None


def act_aliases() -> dict[str, str]:
    out: dict[str, str] = {}
    for s in implemented_sources():
        out.update(s.aliases)
    return out


def sources_for_kind(kind: str) -> list[str]:
    return [s.source_id for s in implemented_sources() if kind in s.kinds]
