"""CJEU case law from Cellar: identifiers, SPARQL metadata rows and the text of a judgment / order / AG opinion.

Identifiers (verified against Cellar 2026-09-28):
- case number `C-260/18` (Court of Justice), `T-123/20` (General Court), `F-…` (Civil Service Tribunal, until
  2016); optional procedural suffix (`C-489/19 PPU`, `C-123/20 P`); Cellar prints the hyphen as U+2011;
- CELEX `6YYYYTTNNNN` (sector 6): CJ judgment, CO order, CC Advocate General opinion, TJ / TO General Court
  judgment / order, FJ / FO Civil Service Tribunal. Notices (CN, CA), summaries (`_SUM`), `_RES` variants and
  joined-case duplicates are NOT stored;
- ECLI `ECLI:EU:C:2019:819`.
Joined cases carry the CELEX of the first case number only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "cellar-case-0.1.0"

_HY = "-‐‑–"  # hyphen-minus, hyphen, non-breaking hyphen, en dash
SUFFIXES = r"(?:P|PPU|RX-II|RX|OP|R|RENV|DEP|INT|AJ|SA|SAM|AO)"
# Building block for service._CASE_RE (no capture groups, no \b: the caller wraps it).
CJEU_CASE_PATTERN = rf"(?<![\w/.-])[CTF][{_HY}]\d{{1,4}}/\d{{2}}(?:\s?{SUFFIXES})?(?![\w/])"
CJEU_CASE_RE = re.compile(CJEU_CASE_PATTERN)
_CASE_PARTS_RE = re.compile(rf"([CTF])[{_HY}](\d{{1,4}})/(\d{{2}})(?:\s?({SUFFIXES}))?", re.I)

ECLI_RE = re.compile(r"\bECLI:EU:[CTF]:\d{4}:\d{1,5}\b", re.I)
CASE_CELEX_RE = re.compile(r"\b6\d{4}(?:CJ|CO|CC|TJ|TO|FJ|FO)\d{4}\b", re.I)
# service: an identifier that is not a case number but still names a CJEU document
CJEU_IDENT_PATTERN = rf"{ECLI_RE.pattern}|{CASE_CELEX_RE.pattern}"

COURT_CJEU = "Trybunał Sprawiedliwości Unii Europejskiej"
COURT_GC = "Sąd Unii Europejskiej"
COURT_CST = "Sąd do spraw Służby Publicznej Unii Europejskiej"
# CELEX type letters -> (court name, court_type, judgment_type)
DOC_TYPES: dict[str, tuple[str, str, str]] = {
    "CJ": (COURT_CJEU, "CJEU", "SENTENCE"),
    "CO": (COURT_CJEU, "CJEU", "DECISION"),
    "CC": (COURT_CJEU, "CJEU", "OPINION"),  # Advocate General opinion: not a ruling
    "TJ": (COURT_GC, "GENERAL_COURT", "SENTENCE"),
    "TO": (COURT_GC, "GENERAL_COURT", "DECISION"),
    "FJ": (COURT_CST, "CIVIL_SERVICE_TRIBUNAL", "SENTENCE"),
    "FO": (COURT_CST, "CIVIL_SERVICE_TRIBUNAL", "DECISION"),
}
_TYPES_BY_LETTER = {"C": ("CJ", "CO", "CC"), "T": ("TJ", "TO"), "F": ("FJ", "FO")}
TYPE_PL = {"CJ": "wyrok", "CO": "postanowienie", "CC": "opinia rzecznika generalnego",
           "TJ": "wyrok", "TO": "postanowienie", "FJ": "wyrok", "FO": "postanowienie"}


def is_case_celex(celex: str) -> bool:
    return bool(CASE_CELEX_RE.fullmatch(celex.strip()))


def canonical_case(text: str) -> str | None:
    """'c‑260/18 p' -> 'C-260/18 P'; None when the text is not one CJEU case number."""
    m = _CASE_PARTS_RE.fullmatch((text or "").strip())
    if not m:
        return None
    letter, no, yy, suf = m.groups()
    return f"{letter.upper()}-{int(no)}/{yy}" + (f" {suf.upper()}" if suf else "")


def case_numbers_in(text: str) -> list[str]:
    """All CJEU case numbers in a string (canonical form, in order, unique)."""
    out: list[str] = []
    for m in CJEU_CASE_RE.finditer(text or ""):
        c = canonical_case(m.group(0))
        if c and c not in out:
            out.append(c)
    return out


def base_case(canon: str) -> str:
    """'C-123/20 P' -> 'C-123/20'."""
    return canon.split(" ")[0]


def case_year(yy: str) -> int:
    return 1900 + int(yy) if int(yy) >= 89 else 2000 + int(yy)  # numbering with the C-/T- prefix starts in 1989


def case_from_celex(celex: str) -> str:
    """'62018CJ0260' -> 'C-260/18' (the first case number of the document); cases before 1989 have no
    court letter ('61962CJ0026' -> '26/62')."""
    no, yy = int(celex[7:]), celex[3:5]
    return f"{no}/{yy}" if int(celex[1:5]) < 1989 else f"{celex[5]}-{no}/{yy}"


def celex_for_case(canon: str) -> list[str]:
    """CELEX numbers a case number can map to (judgment, order, AG opinion...); existence is checked upstream."""
    m = _CASE_PARTS_RE.fullmatch(canon.strip())
    if not m:
        return []
    letter, no, yy, _ = m.groups()
    return [f"6{case_year(yy)}{t}{int(no):04d}" for t in _TYPES_BY_LETTER[letter.upper()]]


def normalize_ecli(text: str) -> str:
    return text.strip().upper()


# --------------------------------------------------------------------------- text


def case_text(content: bytes | str) -> str:
    """Plain text of the judgment. Paragraph numbers, printed by the OJ XHTML on their own line
    (`<p class="count">69</p>`), are joined to the paragraph so quotes and "pkt 69" references work."""
    html = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    lines = html_to_text(html).split("\n")
    out: list[str] = []
    expected = 1
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln == str(expected) and i + 1 < len(lines) and not lines[i + 1].isdigit():
            out.append(f"{ln}. {lines[i + 1]}")
            expected += 1
            i += 2
            continue
        out.append(ln)
        i += 1
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- metadata


@dataclass
class CaseMeta:
    celex: str
    date: date | None = None
    ecli: str | None = None
    title: str = ""  # Cellar expression title, '#'-separated parts
    parties: str | None = None
    case_numbers: list[str] = field(default_factory=list)
    national_court: str | None = None
    keywords: str | None = None
    cites: list[str] = field(default_factory=list)  # CELEX numbers cited by the document
    interprets: list[str] = field(default_factory=list)  # EU legislation the ruling interprets
    subject_codes: list[str] = field(default_factory=list)

    @property
    def doc_type(self) -> str:
        return self.celex[5:7]

    @property
    def parts(self) -> list[str]:
        return [p.strip() for p in self.title.replace("\xa0", " ").split("#") if p.strip()]

    @property
    def header(self) -> str:
        return self.parts[0] if self.parts else self.celex


def _val(b: dict, k: str) -> str | None:
    v = b.get(k)
    return v["value"].replace("\xa0", " ").strip() if v and v.get("value") else None


def _iso(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


def parse_core_rows(bindings: list[dict]) -> dict[str, CaseMeta]:
    """Rows of the core query -> one CaseMeta per CELEX. Cellar returns several rows per document (two
    expressions, two works); the longest title (the one naming all parties) wins, case numbers are merged."""
    out: dict[str, CaseMeta] = {}
    for b in bindings:
        celex = _val(b, "celex")
        if not celex or not is_case_celex(celex):
            continue
        m = out.setdefault(celex, CaseMeta(celex=celex))
        m.date = m.date or _iso(_val(b, "date"))
        m.ecli = m.ecli or _val(b, "ecli")
        title = (_val(b, "title") or "")
        if len(title) > len(m.title):
            m.title = title
        for k, attr in (("parties", "parties"), ("national", "national_court"), ("indicator", "keywords")):
            v = _val(b, k)
            if v and len(v) > len(getattr(m, attr) or ""):
                setattr(m, attr, v)
        for c in case_numbers_in(_val(b, "case") or ""):
            if c not in m.case_numbers:
                m.case_numbers.append(c)
    return out


def parse_relation_rows(bindings: list[dict], meta: CaseMeta) -> None:
    """Rows (rel, val) of the relations query: cited works, interpreted legislation, subject-matter codes."""
    for b in bindings:
        rel, v = _val(b, "rel"), _val(b, "val")
        if not rel or not v:
            continue
        target = {"cites": meta.cites, "interprets": meta.interprets, "subject": meta.subject_codes}.get(rel)
        if target is not None and v not in target:
            target.append(v)


def cited_split(celexes: list[str]) -> tuple[list[str], list[str]]:
    """(cases, legislation): cited CELEX numbers split into CJEU case law (sector 6) and everything else
    (treaties, directives and regulations; consolidated 0… versions are dropped)."""
    cases = sorted(c for c in celexes if is_case_celex(c))
    leg = sorted(c for c in celexes if c[:1] in "1234" and not is_case_celex(c))
    return cases, leg


def build_case(meta: CaseMeta, text: str, *, snapshot_id: str, sha256: str, meta_snapshot_id: str | None,
               original_url: str, source_url: str, language: str, fmt: str, fallback_flags: list[str],
               extra: dict | None = None) -> tuple[Judgment, LegalDocument]:
    court, court_type, jtype = DOC_TYPES[meta.doc_type]
    cnums = list(meta.case_numbers)
    from_source = bool(cnums)
    if not cnums:
        cnums = [case_from_celex(meta.celex)]
    full = list(cnums)
    for c in cnums:  # "C-123/20 P" is found by its base number too, and vice versa
        if base_case(c) not in full:
            full.append(base_case(c))
    if meta.ecli:
        full.append(meta.ecli)
    flags = list(fallback_flags)
    if not from_source:
        flags.append("case_number_from_celex")
    if meta.date is None:
        flags.append("judgment_date_missing")
    if meta.doc_type == "CC":
        flags.append("advocate_general_opinion_not_a_ruling")
    if meta.doc_type in ("TJ", "TO", "FJ", "FO"):
        flags.append("appeal_status_not_checked")
    if len(text) < 500:
        flags.append("text_very_short")
    doc_id = f"celex:{meta.celex}"
    judgment = Judgment(
        document_id=doc_id, source_judgment_id=meta.celex, publisher_id=meta.ecli, court_name=court,
        court_type=court_type, case_numbers=full, judgment_date=meta.date, judgment_type=jtype, text=text,
        original_url=original_url, snapshot_id=snapshot_id, data_quality_flags=flags,
    )
    title = " – ".join(p for p in [meta.header, meta.parties or (meta.parts[1] if len(meta.parts) > 1 else ""),
                                   ", ".join(cnums)] if p)
    cases, legislation = cited_split(meta.cites)
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.eu_judgment, celex=meta.celex, ecli=meta.ecli, title=title,
        publication=meta.ecli, original_url=original_url, snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "celex": meta.celex, "ecli": meta.ecli, "document_type": meta.doc_type,
            "document_type_pl": TYPE_PL[meta.doc_type], "case_numbers": cnums, "parties": meta.parties,
            "keywords": meta.keywords, "national_court": meta.national_court,
            "subject_matter_codes": meta.subject_codes,
            "interpreted_legislation_celex": sorted(c for c in meta.interprets if c[:1] in "1234"),
            "cited_legislation_celex": legislation, "cited_cases_celex": cases,
            "text_language": language, "text_format": fmt, "cellar_url": source_url,
            "metadata_snapshot_id": meta_snapshot_id, "parser_version": PARSER_VERSION,
            "note": ("Tekst z Cellar (Urząd Publikacji UE); wersja dokumentacyjna, nie zastępuje publikacji "
                     "w Zbiorze Orzeczeń. Pole cited_* pochodzi z metadanych Cellar i może być niepełne."),
            **(extra or {}),
        },
    )
    return judgment, doc
