#!/usr/bin/env python3
"""Render the sources table from src/prawnik_mcp/sources/catalog.toml into README files.

Replaces the block between <!-- sources:start --> and <!-- sources:end --> in README.md and README.pl.md.
Run after changing the catalog: python scripts/gen_sources_table.py  (use --check in CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

from prawnik_mcp import sources

ROOT = Path(__file__).resolve().parents[1]
BADGE = {"stable": "stable", "beta": "beta", "experimental": "experimental", "research": "planned"}
KIND = {"statute": "statutes", "judgment": "judgments", "eu_act": "EU acts", "decision": "decisions",
        "tax_ruling": "tax rulings", "eu_judgment": "EU judgments", "registry": "company registry",
        "reference_data": "exchange rates"}
KIND_PL = {"statute": "ustawy", "judgment": "orzeczenia", "eu_act": "akty UE", "decision": "decyzje",
           "tax_ruling": "interpretacje podatkowe", "eu_judgment": "orzeczenia TSUE", "registry": "rejestr podmiotów",
           "reference_data": "kursy walut"}
STATUS_PL = {"stable": "stabilne", "beta": "beta", "experimental": "eksperymentalne", "research": "planowane"}
HEADER = {"README.md": ("Source", "Content", "Status", "Rate limit", "Terms", "terms"),
          "README.pl.md": ("Źródło", "Zawartość", "Status", "Limit zapytań", "Warunki", "warunki")}


def table(readme: str = "README.md") -> str:
    pl = readme == "README.pl.md"
    src, content, status, rate, terms, link = HEADER[readme]
    rows = [f"| {src} | {content} | {status} | {rate} | {terms} |", "|---|---|---|---|---|"]
    order = {"stable": 0, "beta": 1, "experimental": 2, "research": 3}
    for s in sorted(sources.catalog().values(), key=lambda s: (order[s.maturity], s.source_id)):
        kinds = ", ".join((KIND_PL if pl else KIND).get(k, k) for k in s.kinds)
        label = STATUS_PL[s.maturity] if pl else BADGE[s.maturity]
        rows.append(f"| {s.name} | {kinds} | {label} | {s.rate_per_s:g} req/s | [{link}]({s.terms_url}) |")
    return "\n".join(rows)


def main() -> int:
    check = "--check" in sys.argv
    changed = False
    for name in ("README.md", "README.pl.md"):
        p = ROOT / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        start, end = "<!-- sources:start -->", "<!-- sources:end -->"
        if start not in text:
            continue
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        new = f"{head}{start}\n{table(name)}\n{end}{tail}"
        if new != text:
            changed = True
            if not check:
                p.write_text(new, encoding="utf-8")
    if check and changed:
        print("README sources table is out of date: run python scripts/gen_sources_table.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
