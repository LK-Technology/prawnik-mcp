"""Citation edges between documents (judgment -> statute provisions, judgment -> judgments).

Edges are derived from source metadata (SAOS `referencedRegulations` / `referencedCourtCases`), not
from free-text mining, and stored in the `citations` table so incoming citations can be queried
cheaply. An edge whose target is not in the local corpus is kept and reported as `out_of_corpus`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prawnik_mcp.contracts import LegalDocument, canonical_locator

# "(Dz. U. z 1964 r. Nr 16, poz. 93 - art. 23; art. 24 § 1)"
_DZU_REF = re.compile(
    r"\((?:Dz\.\s*U\.|M\.\s*P\.)\s*z\s*(\d{4})\s*r\.\s*(?:Nr\s*\d+\s*,?\s*)?poz\.\s*(\d+)\s*(?:-\s*(.*))?\)\s*$")
_ART = re.compile(r"art\.\s*\d+[a-z]{0,3}(?:\(\d+\)|\^\d+)?(?:\s*(?:§|ust\.|pkt)\s*\d+[a-z]?(?:\(\d+\))?)*", re.I)


@dataclass(frozen=True)
class Edge:
    src: str
    target: str  # document id (eli:DU/1964/93, saos:123) or "case:<number>" when unresolved
    locator: str | None
    kind: str  # "regulation" | "court_case"
    raw: str


def _articles(spec: str | None) -> list[str]:
    out: list[str] = []
    for m in _ART.finditer(spec or ""):
        loc = canonical_locator(m.group(0))
        if loc and loc not in out:
            out.append(loc)
    return out


def edges_for(doc: LegalDocument) -> list[Edge]:
    md = doc.metadata or {}
    edges: list[Edge] = []
    structs = md.get("referenced_regulations_struct")
    regs = structs if structs is not None else [{"text": t} for t in md.get("referenced_regulations") or []]
    for r in regs:
        text = r.get("text") or ""
        year, entry = r.get("year"), r.get("entry")
        spec = None
        m = _DZU_REF.search(text)
        if m:
            year, entry, spec = year or m.group(1), entry or m.group(2), m.group(3)
        if not (year and entry):
            continue
        journal = "MP" if re.search(r"\(\s*M\.\s*P\.", text) else "DU"
        target = f"eli:{journal}/{year}/{entry}"
        arts = _articles(spec)
        for loc in arts or [None]:
            edges.append(Edge(doc.document_id, target, loc, "regulation", text))
    for c in md.get("referenced_court_cases") or []:
        ids = c.get("saos_ids") or []
        targets = [f"saos:{i}" for i in ids] or [f"case:{c.get('case_number')}"]
        for t in targets:
            edges.append(Edge(doc.document_id, t, None, "court_case", c.get("case_number") or ""))
    return edges
