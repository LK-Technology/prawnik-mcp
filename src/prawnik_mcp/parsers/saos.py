"""Parser for SAOS judgment JSON (`GET /api/judgments/{id}`) -> Judgment + LegalDocument."""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "saos-json-0.1.0"
SAOS_PAGE = "https://www.saos.org.pl/judgments/{id}"
MIN_PLAUSIBLE = date(1918, 1, 1)

JUDGMENT_TYPES_PL = {
    "SENTENCE": "Wyrok",
    "DECISION": "Postanowienie",
    "RESOLUTION": "Uchwała",
    "REASONS": "Uzasadnienie",
    "REGULATION": "Zarządzenie",
}


def _court_name(d: dict) -> str:
    ct = d.get("courtType") or ""
    div = d.get("division") or {}
    if div.get("court", {}).get("name"):
        return div["court"]["name"]
    if ct == "SUPREME":
        chambers = ", ".join(c.get("name", "") for c in d.get("chambers") or [] if c.get("name"))
        return "Sąd Najwyższy" + (f" ({chambers})" if chambers else "")
    if ct == "CONSTITUTIONAL_TRIBUNAL":
        return "Trybunał Konstytucyjny"
    if ct == "NATIONAL_APPEAL_CHAMBER":
        return "Krajowa Izba Odwoławcza"
    return ct or "(nieznany sąd)"


def parse_saos_judgment(content: bytes, snapshot_id: str, sha256: str, fetched_at: datetime) -> tuple[Judgment, LegalDocument]:
    payload = json.loads(content.decode("utf-8"))
    d = payload.get("data", payload)
    sid = str(d["id"])
    flags: list[str] = []

    raw_date = d.get("judgmentDate")
    jdate: date | None = None
    if raw_date:
        try:
            jdate = date.fromisoformat(raw_date)
        except ValueError:
            flags.append(f"judgment_date_unparseable:{raw_date}")
    else:
        flags.append("judgment_date_missing")
    if jdate and jdate > fetched_at.date():
        flags.append("judgment_date_in_future")
        flags.append(f"judgment_date_raw:{raw_date}")
        jdate = None
    elif jdate and jdate < MIN_PLAUSIBLE:
        flags.append("judgment_date_implausible")
        flags.append(f"judgment_date_raw:{raw_date}")
        jdate = None

    source = d.get("source") or {}
    pub_id = source.get("judgmentId")
    if jdate is None and pub_id:
        m = re.search(r"_(\d{4}-\d{2}-\d{2})_", pub_id)
        if m:
            # a hint only; never substituted for the judgment date
            flags.append(f"judgment_date_hint_from_publisher_id:{m.group(1)}")

    html = d.get("textContent") or ""
    text = html_to_text(html) if "<" in html else html.strip()
    if not text:
        flags.append("text_empty")

    original = source.get("judgmentUrl")
    if not original:
        flags.append("no_publisher_url")
    case_numbers = [c["caseNumber"] for c in d.get("courtCases") or [] if c.get("caseNumber")]
    court = _court_name(d)
    jtype = d.get("judgmentType") or "UNKNOWN"

    doc_id = f"saos:{sid}"
    judgment = Judgment(
        document_id=doc_id, source_judgment_id=sid, publisher_id=pub_id,
        court_name=court, court_type=d.get("courtType") or "UNKNOWN",
        case_numbers=case_numbers, judgment_date=jdate, judgment_type=jtype, text=text,
        original_url=original, snapshot_id=snapshot_id, data_quality_flags=flags,
    )
    title = " – ".join([
        JUDGMENT_TYPES_PL.get(jtype, jtype), court, ", ".join(case_numbers) or "(brak sygnatury)",
        jdate.isoformat() if jdate else "data niepewna",
    ])
    div = d.get("division") or {}
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.judgment, title=title,
        original_url=original or SAOS_PAGE.format(id=sid),
        snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "saos_id": sid,
            "saos_url": SAOS_PAGE.format(id=sid),
            "court_type": d.get("courtType"),
            "division": div.get("name"),
            "judgment_date_raw": raw_date,
            "keywords": d.get("keywords") or [],
            "legal_bases": d.get("legalBases") or [],
            "referenced_regulations": [r.get("text") for r in d.get("referencedRegulations") or [] if r.get("text")],
            "referenced_regulations_struct": [
                {"year": r.get("journalYear"), "no": r.get("journalNo"), "entry": r.get("journalEntry"),
                 "title": r.get("journalTitle"), "text": r.get("text")}
                for r in d.get("referencedRegulations") or [] if r.get("journalYear") and r.get("journalEntry")],
            "referenced_court_cases": [
                {"case_number": c.get("caseNumber"), "saos_ids": c.get("judgmentIds") or [],
                 "generated": bool(c.get("generated"))}
                for c in d.get("referencedCourtCases") or [] if c.get("caseNumber")],
            "publisher_publication_date": source.get("publicationDate"),
            "source_code": source.get("code"),
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc
