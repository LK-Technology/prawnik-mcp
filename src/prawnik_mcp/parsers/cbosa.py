"""Parser for CBOSA judgment pages (orzeczenia.nsa.gov.pl/doc/{HEX}) -> Judgment + LegalDocument.

HTML contract, checked against live pages on 2026-09-28 (samples in tests/fixtures/raw/cbosa):

- judgment page `/doc/{HEX}`:
  `<TITLE>{sygnatura} - {Rodzaj} {sąd, skrót} z {RRRR-MM-DD}</TITLE>`, header
  `<span class="war_header">{sygnatura} - {Rodzaj} {sąd}</span>`, metadata rows
  `<td class="lista-label">{etykieta}</td> … <td class="info-list-value">{wartość}</td>`
  (the "Data orzeczenia" value also carries "orzeczenie prawomocne" / "orzeczenie nieprawomocne"),
  body sections `<div class="lista-label">{Tezy|Sentencja|Uzasadnienie|…}</div>
  <span class="info-list-value-uzasadnienie">…`.

Result lists (`/cbo/search`, `/cbo/find`) are not parsed: robots.txt disallows them.

The label names and the title/header shape follow the regex contract of mcp-nsa
(https://github.com/matematicsolutions/mcp-nsa, MIT, (c) 2026 MateMatic / Wieslaw Mazur), re-implemented
here in Python. Page text is stored as data only; nothing in it is ever interpreted as an instruction.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "cbosa-html-0.1.0"
BASE_URL = "https://orzeczenia.nsa.gov.pl"
MIN_PLAUSIBLE = date(1918, 1, 1)
COURT_TYPE = "ADMINISTRATIVE"  # same value SAOS uses for NSA/WSA judgments
DISCLAIMER = ("CBOSA służy wyłącznie celom informacyjnym i edukacyjnym i nie ma statusu zbioru urzędowego "
              "(komunikat NSA na orzeczenia.nsa.gov.pl/cbo/query).")

JUDGMENT_TYPES = {"wyrok": "SENTENCE", "postanowienie": "DECISION", "uchwała": "RESOLUTION"}

DOC_ID_RE = re.compile(r"[0-9A-F]{10}")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_HEADER_RE = re.compile(r'<span class="war_header">(.*?)</span>', re.S)
# "III OSK 6859/21 - Wyrok NSA z 2025-03-27" / "II SAB/Łd 23/25 - Wyrok WSA w Łodzi" (header: no date)
_LABEL_RE = re.compile(r"^(?P<case>.+?)\s+-\s+(?P<type>\S+)\s+(?P<court>.+?)(?:\s+z\s+(?P<date>\d{4}-\d{2}-\d{2}))?$")
_ROW_SPLIT_RE = re.compile(r'<tr class="niezaznaczona">')
_ROW_LABEL_RE = re.compile(r'<td class="lista-label">(.*?)</td>', re.S)
_ROW_VALUE_RE = re.compile(r'<td class="info-list-value"[^>]*>', re.S)
_SECTION_RE = re.compile(r'<div class="lista-label">(.*?)</div>\s*<span class="info-list-value-uzasadnienie">', re.S)
_DOC_LINK_RE = re.compile(r'<a\s+href="/doc/([0-9A-Fa-f]{10})"\s*>(.*?)</a>', re.S)
_ACT_RE = re.compile(
    r'<a\s[^>]*href="(?P<url>[^"]*)"[^>]*>(?P<pub>.*?)</a>(?P<prov>[^<]*)'
    r"(?:<br\s*/?>\s*<span class=['\"]nakt['\"]>(?P<act>.*?)</span>)?", re.S)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _plain(fragment: str) -> str:
    """Inline HTML fragment -> one line of text."""
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def doc_url(doc_id: str) -> str:
    return f"{BASE_URL}/doc/{doc_id.upper()}"


def court_full_name(short: str) -> str:
    """'NSA' -> 'Naczelny Sąd Administracyjny'; 'WSA w Łodzi' -> 'Wojewódzki Sąd Administracyjny w Łodzi'."""
    s = short.strip()
    if s == "NSA":
        return "Naczelny Sąd Administracyjny"
    if s.startswith("WSA "):
        return "Wojewódzki Sąd Administracyjny " + s[4:]
    return s


def parse_label(text: str) -> dict[str, str | None]:
    """Split a CBOSA label ("{sygn} - {Rodzaj} {sąd} z {data}") into parts; unknown shape -> case None."""
    m = _LABEL_RE.match(_plain(text))
    if not m:
        return {"case": None, "type": None, "court": None, "date": None}
    return {"case": m["case"], "type": m["type"], "court": m["court"], "date": m["date"]}


# --------------------------------------------------------------------------- judgment page


def is_judgment_page(content: bytes | str) -> bool:
    s = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    return 'class="war_header"' in s and 'class="lista-label"' in s


def _region(s: str) -> str:
    start = s.find('id="res-div"')
    end = s.find("<!-- Stopka", start if start >= 0 else 0)
    if end < 0:
        end = s.find('class="dolne-linki"')
    return s[start if start >= 0 else 0: end if end >= 0 else len(s)]


def parse_judgment_page(content: bytes) -> dict:
    """Raw structured view of a judgment page: title, header, metadata rows, body sections."""
    s = content.decode("utf-8", errors="replace")
    if not is_judgment_page(s):
        raise ValueError("not a CBOSA judgment page (no header/metadata table)")
    tm, hm = _TITLE_RE.search(s), _HEADER_RE.search(s)
    rows: dict[str, str] = {}  # label -> value html
    sections: list[tuple[str, str]] = []  # (label, body html), page order
    for chunk in _ROW_SPLIT_RE.split(_region(s))[1:]:
        if sm := _SECTION_RE.search(chunk):
            sections.append((_plain(sm.group(1)), chunk[sm.end():]))
            continue
        lm = _ROW_LABEL_RE.search(chunk)
        vm = _ROW_VALUE_RE.search(chunk, lm.end()) if lm else None
        if lm and vm:
            rows.setdefault(_plain(lm.group(1)), chunk[vm.end():])
    return {"title": _plain(tm.group(1)) if tm else "", "header": _plain(hm.group(1)) if hm else "",
            "rows": rows, "sections": sections}


def _lines(value_html: str) -> list[str]:
    return [ln.strip() for ln in html_to_text(value_html).split("\n") if ln.strip()]


def _acts(value_html: str) -> list[dict[str, str]]:
    out = []
    for m in _ACT_RE.finditer(value_html):
        item = {"publication": _plain(m["pub"]), "provisions": _plain(m["prov"]), "act": _plain(m["act"] or "")}
        url = htmllib.unescape(m["url"])
        if url.startswith(("http://isap.sejm.gov.pl", "https://isap.sejm.gov.pl")):
            item["isap_url"] = url
        out.append(item)
    return out


def parse_cbosa_judgment(content: bytes, doc_id: str, snapshot_id: str, sha256: str,
                         fetched_at: datetime) -> tuple[Judgment, LegalDocument]:
    doc_id = doc_id.upper()
    if not DOC_ID_RE.fullmatch(doc_id):
        raise ValueError(f"invalid CBOSA document id {doc_id!r}")
    page = parse_judgment_page(content)
    rows: dict[str, str] = page["rows"]
    flags: list[str] = []

    title_parts, header_parts = parse_label(page["title"]), parse_label(page["header"])
    case = header_parts["case"] or title_parts["case"]
    case_numbers = [c.strip() for c in (case or "").split(",") if c.strip()]
    if not case_numbers:
        flags.append("case_number_missing")
    type_pl = header_parts["type"] or title_parts["type"] or ""
    court_short = header_parts["court"] or title_parts["court"] or ""

    # date + finality ("Data orzeczenia" row: "2025-03-27 orzeczenie prawomocne")
    date_text = " ".join(_lines(rows.get("Data orzeczenia", "")))
    raw_date = (m.group(1) if (m := _DATE_RE.search(date_text)) else None) or title_parts["date"]
    if not _DATE_RE.search(date_text):
        flags.append("judgment_date_missing_in_metadata")
    jdate: date | None = None
    if raw_date:
        try:
            jdate = date.fromisoformat(raw_date)
        except ValueError:
            flags.append(f"judgment_date_unparseable:{raw_date}")
    else:
        flags.append("judgment_date_missing")
    if title_parts["date"] and raw_date and title_parts["date"] != raw_date:
        flags.append(f"judgment_date_title_mismatch:{title_parts['date']}")
    if jdate and jdate > fetched_at.date():
        flags += ["judgment_date_in_future", f"judgment_date_raw:{raw_date}"]
        jdate = None
    elif jdate and jdate < MIN_PLAUSIBLE:
        flags += ["judgment_date_implausible", f"judgment_date_raw:{raw_date}"]
        jdate = None
    low = date_text.lower()
    finality_raw = ("orzeczenie nieprawomocne" if "nieprawomocne" in low
                    else "orzeczenie prawomocne" if "prawomocne" in low else None)
    finality = {"orzeczenie nieprawomocne": "not_final", "orzeczenie prawomocne": "final"}.get(finality_raw or "", "unknown")

    court_rows = _lines(rows.get("Sąd", ""))
    court = court_rows[0] if court_rows else court_full_name(court_short)
    if not court_rows:
        flags.append("court_from_title")
    if not court:
        court = "(nieznany sąd)"
        flags.append("court_missing")

    parts = []
    for label, body in page["sections"]:
        txt = html_to_text(body)
        if txt:
            parts.append(f"{label}\n{txt}")
    text = "\n\n".join(parts)
    if not text:
        flags.append("text_empty")

    jtype = JUDGMENT_TYPES.get(type_pl.lower(), type_pl or "UNKNOWN")
    url = doc_url(doc_id)
    document_id = f"cbosa:{doc_id}"
    judgment = Judgment(
        document_id=document_id, source_judgment_id=doc_id, publisher_id=doc_id,
        court_name=court, court_type=COURT_TYPE, case_numbers=case_numbers, judgment_date=jdate,
        judgment_type=jtype, text=text, finality=finality, original_url=url, snapshot_id=snapshot_id,
        data_quality_flags=flags,
    )
    title = " – ".join([type_pl or "Orzeczenie", court, ", ".join(case_numbers) or "(brak sygnatury)",
                        jdate.isoformat() if jdate else "data niepewna"])
    related = [{"document_id": f"cbosa:{h.upper()}", "label": _plain(lbl)}
               for h, lbl in _DOC_LINK_RE.findall(rows.get("Sygn. powiązane", ""))]
    doc = LegalDocument(
        document_id=document_id, kind=SourceKind.judgment, title=title, original_url=url,
        snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "cbosa_id": doc_id,
            "page_title": page["title"],
            "court_short": court_short,
            "court_level": "NSA" if court_short.startswith("NSA") else "WSA" if court_short.startswith("WSA") else None,
            "judgment_type_pl": type_pl,
            "judgment_date_raw": raw_date,
            "finality_raw": finality_raw,
            "finality_as_of": fetched_at.date().isoformat(),  # CBOSA status at fetch time; may change later
            "receipt_date": next(iter(_DATE_RE.findall(" ".join(_lines(rows.get("Data wpływu", ""))))), None),
            "judges": _lines(rows.get("Sędziowie", "")),
            "symbols": _lines(rows.get("Symbol z opisem", "")),
            "keywords": _lines(rows.get("Hasła tematyczne", "")),
            "related": related,
            "challenged_authority": " ".join(_lines(rows.get("Skarżony organ", ""))) or None,
            "outcome": " ".join(_lines(rows.get("Treść wyniku", ""))) or None,
            "legal_bases": _lines(rows.get("Powołane przepisy", "")),
            "referenced_acts": _acts(rows.get("Powołane przepisy", "")),
            "sections": [lbl for lbl, _ in page["sections"]],
            "other_fields": {k: " ".join(_lines(v)) for k, v in rows.items() if k not in _KNOWN_ROWS},
            "source_note": DISCLAIMER,
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc


_KNOWN_ROWS = {"Data orzeczenia", "Data wpływu", "Sąd", "Sędziowie", "Symbol z opisem", "Hasła tematyczne",
               "Sygn. powiązane", "Skarżony organ", "Treść wyniku", "Powołane przepisy"}
