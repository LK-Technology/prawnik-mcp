"""Parsers for KIO (Krajowa Izba Odwoławcza) rulings from the UZP portal orzeczenia.uzp.gov.pl.

Three kinds of pages (stdlib regex / html.parser only):

1. search results – the HTML fragment returned by `POST /Home/GetResults` (10 `div.search-list-item`
   per page, each with `<label>` fields and a link to `/Home/Details/{id}`);
2. metrics – `GET /Home/Details/{id}`: the structured metadata (source of truth for date, document
   type, case numbers, outcome, PZP provisions, thematic index). Nothing is guessed from the text;
3. text – `GET /Home/ContentHtml/{id}?Kind=KIO&flection=0`: Word-export HTML of the ruling.

Ruling text is data, never instructions: it is converted to plain text and stored as-is.

Portions adapted from kio-orzeczenia-mcp (https://github.com/matematicsolutions/kio-orzeczenia-mcp),
Copyright MateMatic (Wiesław Mazur), Apache License 2.0: endpoint paths, search form field names,
label-based metadata extraction, `ł`-aware diacritics folding and Polish month names. Rewritten
without selectolax for this project (sync, regex/stdlib, Judgment/LegalDocument contracts).
"""

from __future__ import annotations

import html as htmllib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "kio-html-0.1.0"
BASE_URL = "https://orzeczenia.uzp.gov.pl"
SEARCH_URL = f"{BASE_URL}/Home/GetResults"
COURT_NAME = "Krajowa Izba Odwoławcza"
COURT_TYPE = "NATIONAL_APPEAL_CHAMBER"  # same value SAOS uses for KIO
PAGE_SIZE = 10  # fixed by UZP
MIN_PLAUSIBLE = date(2004, 1, 1)

# UZP "Rodzaj dokumentu" -> judgment_type (SAOS vocabulary, so KIO from SAOS and from UZP agree)
DOC_TYPES = {"wyrok": "SENTENCE", "postanowienie": "DECISION", "uchwala": "RESOLUTION"}
DOC_TYPES_PL = {"SENTENCE": "Wyrok", "DECISION": "Postanowienie", "RESOLUTION": "Uchwała"}

PL_MONTHS = {
    "stycz": 1, "lut": 2, "marc": 3, "kwiet": 4, "maj": 5, "czerw": 6, "lip": 7, "sierp": 8,
    "wrze": 9, "pazdz": 10, "listop": 11, "grud": 12,
}

_SIG_RE = re.compile(r"\bKIO\s*(\d{1,5})\s*/\s*(\d{2}|\d{4})\b", re.IGNORECASE)
_DETAILS_ID_RE = re.compile(r"/Home/Details/(\d+)", re.IGNORECASE)
_TOTAL_RE = re.compile(r"Liczba znalezionych dokument\w*\s*:\s*([\d\s ]+)", re.IGNORECASE)
_DMY_RE = re.compile(r"\b(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_BIDI_RE = re.compile(r"[‎‏﻿]|&(?:lrm|rlm);|&#(?:8206|8207|x200[eEfF]);")


def details_url(internal_id: int | str) -> str:
    return f"{BASE_URL}/Home/Details/{internal_id}"


def content_url(internal_id: int | str) -> str:
    return f"{BASE_URL}/Home/ContentHtml/{internal_id}?Kind=KIO&flection=0"


def pdf_url(internal_id: int | str) -> str:
    return f"{BASE_URL}/Home/PdfContent/{internal_id}?Kind=KIO"


# --------------------------------------------------------------------------- helpers


def fold(text: str) -> str:
    """ASCII folding for label comparison (`ł` has no NFKD decomposition, so it is mapped by hand)."""
    text = text.replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _plain(fragment: str) -> str:
    """Inline HTML fragment -> one line of text."""
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _key(label_html: str) -> str:
    return fold(_plain(label_html)).rstrip(":").strip().lower()


def normalize_signature(raw: str) -> str | None:
    m = _SIG_RE.search(raw or "")
    return f"KIO {int(m.group(1))}/{m.group(2)}" if m else None


def signatures(raw: str) -> list[str]:
    """All KIO case numbers in a field, e.g. 'KIO 1550/25 | KIO 1581/25' (joined cases)."""
    out: list[str] = []
    for m in _SIG_RE.finditer(raw or ""):
        s = f"KIO {int(m.group(1))}/{m.group(2)}"
        if s not in out:
            out.append(s)
    return out


def parse_pl_date(text: str | None) -> date | None:
    """'15-05-2025', '15.05.2025', '2025-05-15' or '15 maja 2025'. '-' or garbage -> None."""
    if not text or text.strip() in {"-", "--"}:
        return None
    for rx, order in ((_ISO_RE, (1, 2, 3)), (_DMY_RE, (3, 2, 1))):
        m = rx.search(text)
        if m:
            try:
                return date(int(m.group(order[0])), int(m.group(order[1])), int(m.group(order[2])))
            except ValueError:
                return None
    m = re.search(r"(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})", text)
    if m:
        word = fold(m.group(2)).lower()
        for prefix, num in PL_MONTHS.items():
            if word.startswith(prefix):
                try:
                    return date(int(m.group(3)), num, int(m.group(1)))
                except ValueError:
                    return None
    return None


def labelled_fields(block: str) -> dict[str, str]:
    """`<label>Name</label> value` pairs inside a block -> {"name": "value"} (first occurrence wins).

    The value is the text after the label up to the end of the enclosing `<p>`/`<div>` or the next label.
    """
    out: dict[str, str] = {}
    for part in re.split(r"<label\b[^>]*>", block)[1:]:
        label, sep, rest = part.partition("</label>")
        if not sep:
            continue
        key = _key(label)
        value_html = re.split(r"</p>|</div>|<label\b", rest, maxsplit=1)[0]
        if key and not out.get(key):
            if "<li" in value_html:
                out[key] = " ;; ".join(_plain(li) for li in re.findall(r"<li\b[^>]*>(.*?)</li>", value_html, re.S))
            else:
                out[key] = _plain(value_html)
    return out


# --------------------------------------------------------------------------- search results


@dataclass
class KioSearchItem:
    internal_id: str
    case_numbers: list[str]
    doc_type: str | None
    issue_date: date | None
    issue_date_raw: str | None
    organ: str | None
    snippet: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class KioSearchPage:
    total: int
    items: list[KioSearchItem]

    @property
    def pages(self) -> int:
        return -(-self.total // PAGE_SIZE)


def parse_search_results(content: bytes | str) -> KioSearchPage:
    """Parse the `POST /Home/GetResults` fragment. Raises ValueError if it is not a result list
    (a 200 with the page shell but no results block is a layout change, not "0 hits")."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    has_counter = 'id="resultCounts"' in text or _TOTAL_RE.search(text)
    chunks = re.split(r'<div\s+class="search-list-item"', text)[1:]
    if not has_counter and not chunks:
        raise ValueError("unexpected KIO search response (no result counter, no result items)")
    items: list[KioSearchItem] = []
    seen: set[str] = set()
    for chunk in chunks:
        m = _DETAILS_ID_RE.search(chunk)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        f = labelled_fields(chunk)
        frag = re.search(r'<p\s+class="fragment"\s*>(.*?)</p>', chunk, re.S)
        raw_date = f.get("data wydania")
        items.append(KioSearchItem(
            internal_id=m.group(1), case_numbers=signatures(f.get("sygnatura", "")),
            doc_type=(f.get("rodzaj dokumentu") or "").lower() or None,
            issue_date=parse_pl_date(raw_date), issue_date_raw=raw_date, organ=f.get("organ wydajacy"),
            snippet=_plain(frag.group(1)) if frag else "", fields=f,
        ))
    total = len(items)
    m = _TOTAL_RE.search(text)
    if m and re.sub(r"\D", "", m.group(1)):
        total = int(re.sub(r"\D", "", m.group(1)))
    else:
        m = re.search(r'value="([\d,]+)"\s+id="resultCounts"', text)
        if m and len(m.group(1).split(",")) == 5:
            total = int(m.group(1).split(",")[1])  # ALL,KIO,SO,SA,SN
    return KioSearchPage(total=total, items=items)


# --------------------------------------------------------------------------- metrics page


def parse_details(content: bytes | str) -> dict:
    """Parse `/Home/Details/{id}`. Raises ValueError when the metrics block is missing."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    start = text.find('class="details-metrics"')
    if start < 0:
        raise ValueError("unexpected KIO details page (no details-metrics block)")
    end = text.find('id="iframeContent"', start)
    block = text[start:end if end > 0 else len(text)]
    f = labelled_fields(block)

    sig_key = next((k for k in f if k.startswith("sygnatura akt")), None)
    sig_raw = f.get(sig_key, "") if sig_key else ""
    case_numbers = signatures(sig_raw)
    outcomes: list[str] = []
    for li in sig_raw.split(" ;; "):
        parts = re.split(r"\s+/\s+", li, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            o = re.sub(r"\s+", " ", parts[1].replace("*", "")).strip()
            outcomes.append(re.sub(r"\s+(?=kio\s*\d+\s*/\s*\d+\s*:)", "; ", o, flags=re.IGNORECASE))
    if not case_numbers:
        h = re.search(r'<h2 class="section-title"[^>]*>(.*?)<a\b', text, re.S)
        case_numbers = signatures(_plain(h.group(1))) if h else []

    pzp_articles: list[str] = []
    subject_index: list[str] = []
    for title, body in re.findall(r'<a\b[^>]*\btitle="([^"]*)"[^>]*>(.*?)</a>', block, re.S):
        t = fold(htmllib.unescape(title)).lower()
        target = pzp_articles if "dla artykulu" in t else subject_index if "dla indeksu tematycznego" in t else None
        if target is None:
            continue
        for v in re.split(r"\s*\|\s*", _plain(body)):
            v = v.strip(" ;")
            if v and v not in target:
                target.append(v)

    def val(key: str) -> str | None:
        return (f.get(key) or "").strip() or None

    raw_date = val("data wydania rozstrzygniecia")
    return {
        "case_numbers": case_numbers,
        "signature_field": sig_raw or None,
        "outcome": "; ".join(outcomes) or None,
        "organ": val("organ wydajacy"),
        "doc_type": (val("rodzaj dokumentu") or "").lower() or None,
        "issue_date_raw": raw_date,
        "issue_date": parse_pl_date(raw_date),
        "chairman": val("przewodniczacy"),
        "purchaser": val("zamawiajacy"),
        "city": val("miejscowosc"),
        "procedure": val("tryb postepowania"),
        "contract_type": val("rodzaj zamowienia"),
        "pzp_articles": pzp_articles,
        "subject_index": subject_index,
    }


# --------------------------------------------------------------------------- ruling text


def content_to_text(content: bytes | str) -> str:
    """Word-export HTML -> plain text. Each paragraph/line break becomes one line; words unchanged.

    Invisible bidi marks (U+200E/U+200F, BOM) that the export puts at line starts are removed so that
    quotes match; nothing else is altered (line-end hyphenation such as "wnie-\\nsionego" is kept –
    citation checks normalise it)."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    text = _BIDI_RE.sub("", text)
    out = html_to_text(text)
    return "\n".join(ln.strip() for ln in out.split("\n") if ln.strip())


def _date_hint(text: str) -> date | None:
    m = re.search(r"(?:z\s+dnia|dnia|Warszawa,)\s+(\d{1,2}\s+[^\W\d_]+\s+\d{4})", text[:1500])
    return parse_pl_date(m.group(1)) if m else None


def parse_kio_ruling(details_html: bytes, content_html: bytes, internal_id: int | str, *, snapshot_id: str,
                     sha256: str, fetched_at: datetime, details_snapshot_id: str | None = None,
                     ) -> tuple[Judgment, LegalDocument]:
    """Build Judgment + LegalDocument. The text snapshot (ContentHtml) is the document snapshot;
    the metrics snapshot id is kept in metadata."""
    iid = str(internal_id)
    d = parse_details(details_html)
    text = content_to_text(content_html)
    flags: list[str] = []
    if not text:
        flags.append("text_empty")

    case_numbers = list(d["case_numbers"])
    if not case_numbers:
        found = signatures(text[:600])
        if found:
            case_numbers = found
            flags.append("case_numbers_from_text")
        else:
            flags.append("case_number_missing")

    jdate = d["issue_date"]
    raw_date = d["issue_date_raw"]
    if jdate is None:
        flags.append("judgment_date_missing" if not raw_date or raw_date.strip() in {"-", "--"}
                     else f"judgment_date_unparseable:{raw_date}")
    elif jdate > fetched_at.date():
        flags += ["judgment_date_in_future", f"judgment_date_raw:{raw_date}"]
        jdate = None
    elif jdate < MIN_PLAUSIBLE:
        flags += ["judgment_date_implausible", f"judgment_date_raw:{raw_date}"]
        jdate = None
    if jdate is None and (hint := _date_hint(text)):
        flags.append(f"judgment_date_hint_from_text:{hint.isoformat()}")  # a hint only, never substituted

    organ = d["organ"] or COURT_NAME
    court_type = COURT_TYPE
    if "izba odwolawcza" not in fold(organ).lower():
        court_type = "UNKNOWN"
        flags.append(f"organ_not_kio:{organ}")
    jtype = DOC_TYPES.get(fold(d["doc_type"] or ""), (d["doc_type"] or "UNKNOWN").upper())
    doc_id = f"kio:{iid}"
    url = details_url(iid)
    judgment = Judgment(
        document_id=doc_id, source_judgment_id=iid, publisher_id=iid, court_name=organ, court_type=court_type,
        case_numbers=case_numbers, judgment_date=jdate, judgment_type=jtype, text=text,
        original_url=url, snapshot_id=snapshot_id, data_quality_flags=flags,
    )
    title = " – ".join([
        DOC_TYPES_PL.get(jtype, d["doc_type"] or jtype), organ, ", ".join(case_numbers) or "(brak sygnatury)",
        jdate.isoformat() if jdate else "data niepewna",
    ])
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.judgment, title=title, original_url=url,
        snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "uzp_id": iid,
            "content_url": content_url(iid),
            "pdf_url": pdf_url(iid),
            "details_snapshot_id": details_snapshot_id,
            "document_type_pl": d["doc_type"],
            "judgment_date_raw": raw_date,
            "outcome": d["outcome"],
            "chairman": d["chairman"],
            "purchaser": d["purchaser"],
            "city": d["city"],
            "procedure": d["procedure"],
            "contract_type": d["contract_type"],
            "pzp_articles": d["pzp_articles"],
            "subject_index": d["subject_index"],
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc
