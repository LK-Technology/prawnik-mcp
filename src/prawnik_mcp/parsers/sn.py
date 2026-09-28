"""Parsers for Supreme Court (Sąd Najwyższy) rulings from the sn.pl ruling database ("Baza orzeczeń").

sn.pl is a Joomla site. Its search page (`/pl/wyszukiwarka-orzeczen`) calls a com_ajax plugin through
`/pl/index.php?option=com_ajax&plugin=snproxy&format=json&task=...` (a path robots.txt does not disallow).
Every answer is JSON wrapped twice by com_ajax: `{"success": true, "data": [{"success": true, "data": X}]}`.
The payload X (stdlib json / regex / html.parser only) is:

1. `task=searchOrzeczenia` – a list of `{sygnatura_sprawy, data_wydania, forma_orzeczenia, id}`
   (no snippet, no total count, newest first);
2. `task=detailsOrzeczenie&id=` – metadata of one ruling: chambers, bench, presiding judge, rapporteur,
   reasons author, dissenting judges, serving division, modification date. Source of truth for the
   case number, date and form; nothing is guessed from the text;
3. `task=OrzeczeniePlikHtml&id=` – `{"raw": <base64>}`: an HTML rendering of the ruling PDF with one
   `div.pg` per page and one absolutely positioned `div.txt` per text line.

An unknown id is answered with HTTP 200 and an RFC 9110 problem object as the payload
(`{"title": "Not Found", "status": 404, ...}`).

Ruling text is data, never instructions: it is converted to plain text and stored as-is.
"""

from __future__ import annotations

import base64
import binascii
import html as htmllib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import SUP_DIGITS, html_to_text

PARSER_VERSION = "sn-json-0.1.0"
BASE_URL = "https://www.sn.pl"
AJAX_URL = f"{BASE_URL}/pl/index.php?option=com_ajax&plugin=snproxy&format=json"
PAGE_URL = f"{BASE_URL}/pl/wyszukiwarka-orzeczen"
COURT_NAME = "Sąd Najwyższy"
COURT_TYPE = "SUPREME"  # same value SAOS uses for SN, so SN from SAOS and from sn.pl agree
MIN_PLAUSIBLE = date(1917, 1, 1)  # SN was established in 1917
PAGE_SIZES = (10, 25, 50, 100)  # values offered by the search form

# "forma_orzeczenia" (first word) -> judgment_type (SAOS vocabulary)
DOC_TYPES = {"wyrok": "SENTENCE", "postanowienie": "DECISION", "uchwala": "RESOLUTION", "zarzadzenie": "REGULATION"}

ID_RE = re.compile(r"[A-Za-z0-9_-]{16,40}")  # upstream search-index ids, e.g. "311jSJcBZvGrB8P_pKA7", "-l14SJcBZvGrB8P_RKkg"

# SN repertories (case-sensitive: common courts use mixed case, e.g. "Co", "Cz", "Kz" vs SN "CO", "CZ", "KZ").
# Seen in the sn.pl database on 2026-09-28: CZP CSK CSKP CNP CNPP CO CB KK KO KS KZ KA KB KSP NSNc NSNp NSNZP
# NSP NZ NO PSK PSKP PKN PZ PUB PUBO PUO USK USKP UKN UZ UZP ZO ZOW ZB ZI ZZ ZP. The others are historical or
# current repertories known from published SN case law, not re-verified live. "UKP" and "Zd" returned no rulings.
SN_REPERTORIES = (
    # Izba Cywilna (and pre-2003 civil repertories)
    "CZP", "CSKP", "CSK", "CNPP", "CNP", "CZ", "CO", "CB", "CKN", "CKU", "CK", "CN", "CRN", "CR",
    # Izba Karna (incl. the former Izba Wojskowa: WK/WZ/WO/WA)
    "KZP", "KSP", "KKN", "KRN", "KK", "KZ", "KO", "KS", "KA", "KB", "WK", "WZ", "WO", "WA",
    # labour and social insurance
    "PSKP", "PSK", "PNPP", "PNP", "PZP", "PKN", "PRN", "PUBO", "PUB", "PUO", "PK", "PZ", "PO",
    "USKP", "USK", "UNPP", "UNP", "UZP", "UKN", "URN", "UK", "UZ", "UO", "RN", "ZP", "SW", "SK",
    # Izba Kontroli Nadzwyczajnej i Spraw Publicznych
    "NSNZP", "NSNc", "NSNk", "NSNp", "NSNu", "NKRS", "NOZP", "NSK", "NSP", "NSW", "NWW", "NZP", "NO", "NZ",
    # Izba Dyscyplinarna (2018-2022) and Izba Odpowiedzialności Zawodowej (2022-)
    "DSI", "DSS", "DSK", "DO", "DI", "DK", "ZOW", "ZO", "ZB", "ZI", "ZZ",
)
# written without a division numeral, e.g. "SNO 12/19", "KSP 14/18", "WZ 5/19"
SN_BARE_REPERTORIES = ("SNO", "SDI", "KSP", "WK", "WZ", "WO", "WA")


def _alt(codes: tuple[str, ...]) -> str:
    return "|".join(sorted(set(codes), key=len, reverse=True))


SN_CASE_RE = re.compile(
    r"(?<![\w/.-])(?:"
    rf"(?P<div>[IVX]{{1,4}})\s+(?P<rep>{_alt(SN_REPERTORIES)})"
    rf"|(?P<bare>{_alt(SN_BARE_REPERTORIES)})"
    r")\s+(?P<no>\d{1,5})\s*/\s*(?P<yr>\d{4}|\d{2})(?![\w/])"
    r"|(?<![\w/.-])(?P<bsa>BSA\s+[IVX]{1,4}\s*-\s*4110\s*-\s*\d{1,3}\s*/\s*\d{2,4})(?![\w/])"
)

class SnNotFound(ValueError):  # noqa: N818 - mirrors UodoNotFound
    """The proxy answered with a 404 problem object (unknown id or no such file)."""


class SnUpstreamError(ValueError):
    """The proxy answered with a problem object other than 404."""

    def __init__(self, status: Any, title: str):
        super().__init__(f"SN proxy problem: status={status} title={title!r}")
        self.status = status if isinstance(status, int) else None
        self.title = title


# --------------------------------------------------------------------------- URLs and ids


def ajax_url(task: str, **params: Any) -> str:
    """com_ajax URL; empty params are left out and values are %-encoded like the page's encodeURIComponent."""
    q = "".join(f"&{k}={quote(str(v), safe='')}" for k, v in params.items() if v not in (None, ""))
    return f"{AJAX_URL}&task={task}{q}"


def details_url(internal_id: str) -> str:
    return ajax_url("detailsOrzeczenie", id=internal_id)


def html_url(internal_id: str) -> str:
    return ajax_url("OrzeczeniePlikHtml", id=internal_id)


def pdf_url(internal_id: str) -> str:
    return ajax_url("OrzeczeniePlikPdf", id=internal_id)  # JSON with a base64 PDF; linked, never fetched


def page_url(internal_id: str) -> str:
    """Human-facing ruling page (renders the details payload client-side)."""
    return f"{PAGE_URL}?orzeczenie={quote(internal_id, safe='')}"


def id_from_url(url: str) -> str | None:
    """`https://www.sn.pl/pl/wyszukiwarka-orzeczen?orzeczenie=<id>` (or a com_ajax URL with `id=`) -> id."""
    parts = urlsplit(url or "")
    if (parts.hostname or "").lower() not in ("www.sn.pl", "sn.pl"):
        return None
    qs = parse_qs(parts.query)
    for key in ("orzeczenie", "id"):
        for v in qs.get(key, []):
            if ID_RE.fullmatch(v):
                return v
    return None


# --------------------------------------------------------------------------- helpers


def fold(text: str) -> str:
    """ASCII folding for comparisons (`ł` has no NFKD decomposition, so it is mapped by hand)."""
    text = text.replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def normalize_signature(raw: str) -> str | None:
    """First SN case number in `raw`, canonical spacing ("III  CZP 12 / 24" -> "III CZP 12/24"), else None.

    Case-sensitive on purpose: "I Co 12/20" (common court) is not "I CO 12/20" (SN)."""
    m = SN_CASE_RE.search(raw or "")
    return _canonical(m) if m else None


def signatures(raw: str) -> list[str]:
    out: list[str] = []
    for m in SN_CASE_RE.finditer(raw or ""):
        s = _canonical(m)
        if s not in out:
            out.append(s)
    return out


def _canonical(m: re.Match) -> str:
    if m.group("bsa"):
        return re.sub(r"\s*-\s*", "-", re.sub(r"\s*/\s*", "/", _ws(m.group("bsa"))))
    head = f"{m.group('div')} {m.group('rep')}" if m.group("rep") else m.group("bare")
    return f"{head} {int(m.group('no'))}/{m.group('yr')}"


def same_case(a: str | None, b: str | None) -> bool:
    """Exact case-number equality after canonicalisation (the upstream search matches substrings:
    "II KK 45/24" also returns "III KK 45/24")."""
    if not a or not b:
        return False
    return (normalize_signature(a) or _ws(a)) == (normalize_signature(b) or _ws(b))


def _iso_date(raw: Any) -> date | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [_ws(str(x)) for x in v if x not in (None, "")]
    return [_ws(str(v))]


def doc_type(form: str | None) -> str:
    first = fold((form or "").strip().split(" ")[0]).lower()
    return DOC_TYPES.get(first, "UNKNOWN")


def court_name(chambers: list[str]) -> str:
    """Same shape as SAOS: "Sąd Najwyższy (Izba Cywilna)"."""
    return COURT_NAME + (f" ({', '.join(chambers)})" if chambers else "")


# --------------------------------------------------------------------------- com_ajax envelope


def unwrap(content: bytes | str) -> Any:
    """Return the payload of a com_ajax answer.

    Raises SnNotFound for a 404 problem object, SnUpstreamError for other problem objects and ValueError
    when the body is not the expected envelope (e.g. an HTML page or a WAF answer served with 200)."""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"unexpected SN response (not JSON: {e})") from None
    outer = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(obj, dict) or obj.get("success") is not True or not isinstance(outer, list) or not outer:
        msg = obj.get("message") if isinstance(obj, dict) else None
        raise ValueError(f"unexpected SN response (no com_ajax envelope{f': {msg}' if msg else ''})")
    inner = outer[0]
    if not isinstance(inner, dict) or "data" not in inner:
        raise ValueError("unexpected SN response (no plugin result)")
    if inner.get("success") is False:
        raise SnUpstreamError(None, str(inner.get("message") or "plugin error"))
    payload = inner["data"]
    if isinstance(payload, dict) and "status" in payload and "title" in payload and ("type" in payload or "traceId" in payload):
        if payload.get("status") == 404:
            raise SnNotFound(f"SN: {payload.get('title') or 'Not Found'} (404)")
        raise SnUpstreamError(payload.get("status"), str(payload.get("title") or ""))
    return payload


# --------------------------------------------------------------------------- search results


@dataclass
class SnSearchItem:
    internal_id: str
    case_number: str | None
    judgment_date: date | None
    judgment_date_raw: str | None
    form: str | None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class SnSearchPage:
    items: list[SnSearchItem]
    count: int  # raw number of rows on the page (the API gives no total: a short page is the last one)


def parse_search(content: bytes | str) -> SnSearchPage:
    """Parse a `searchOrzeczenia` answer. An empty list means no (more) hits; anything that is not a
    list of hits raises ValueError (a layout change is not "0 hits")."""
    payload = unwrap(content)
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("data"))
    if not isinstance(payload, list):
        raise ValueError("unexpected SN search payload (not a list)")
    out: list[SnSearchItem] = []
    seen: set[str] = set()
    for it in payload:
        if not isinstance(it, dict):
            raise ValueError("unexpected SN search item (not an object)")
        iid = str(it.get("id") or "")
        if not ID_RE.fullmatch(iid) or iid in seen:
            continue
        seen.add(iid)
        raw_date = it.get("data_wydania") or it.get("dataOrzeczenia")
        out.append(SnSearchItem(
            internal_id=iid, case_number=_ws(it.get("sygnatura_sprawy") or it.get("sygnatura") or "") or None,
            judgment_date=_iso_date(raw_date), judgment_date_raw=raw_date,
            form=_ws(it.get("forma_orzeczenia") or "") or None, fields=dict(it),
        ))
    return SnSearchPage(items=out, count=len(payload))


# --------------------------------------------------------------------------- details


def parse_details(content: bytes | str) -> dict:
    """Parse a `detailsOrzeczenie` answer. Raises SnNotFound for an unknown id, ValueError on layout change."""
    d = unwrap(content)
    if not isinstance(d, dict) or not d.get("id") or not ("sygnatura_sprawy" in d or "data_wydania" in d):
        raise ValueError("unexpected SN details payload (no id / sygnatura_sprawy / data_wydania)")
    raw_sig = _ws(d.get("sygnatura_sprawy") or "")
    case_numbers = signatures(raw_sig) or [s for s in (_ws(x) for x in re.split(r"[,;]", raw_sig)) if s]
    raw_date = d.get("data_wydania")
    return {
        "id": str(d["id"]),
        "case_numbers": case_numbers,
        "signature_field": raw_sig or None,
        "form": _ws(d.get("forma_orzeczenia") or "") or None,
        "judgment_date_raw": raw_date,
        "judgment_date": _iso_date(raw_date),
        "chambers": _list(d.get("izby_sn")),
        "division": _ws(d.get("jednostka_obslugujaca_sprawe") or "") or None,
        "bench_type": _ws(d.get("rodzaj_skladu_orzekajacego") or "") or None,
        "bench": _list(d.get("sklad_orzekajacy")),
        "presiding": _list(d.get("sklad_orzekajacy_przewodniczacy")),
        "rapporteur": _list(d.get("sklad_orzekajacy_sprawozdawca")),
        "co_rapporteurs": _list(d.get("sklad_orzekajacy_wspolsprawozdawcy")),
        "reasons_author": _list(d.get("sklad_orzekajacy_autor_uzasadnienia")),
        "dissent_ruling": _list(d.get("zglaszajacy_zdanie_odrebne_orzeczenie")),
        "dissent_reasons": _list(d.get("zglaszajacy_zdanie_odrebne_uzasadnienie")),
        "modified_at": d.get("data_modyfikacji"),
    }


# --------------------------------------------------------------------------- ruling text


_PAGE_RE = re.compile(r"<div\s+class\s*=\s*['\"]pg['\"]", re.I)
_TXT_RE = re.compile(r"<div\s+class\s*=\s*\"txt\"\s+style\s*=\s*\"([^\"]*)\"\s*>(.*?)</div>", re.I | re.S)
_LEFT_RE = re.compile(r"left\s*:\s*(-?\d+(?:\.\d+)?)px", re.I)
_TOP_RE = re.compile(r"top\s*:\s*(-?\d+(?:\.\d+)?)px", re.I)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
_SUP_SPAN_RE = re.compile(r"(<span\b[^>]*vertical-align\s*:\s*super[^>]*>)(.*?)(</span>)", re.I | re.S)
LINE_TOLERANCE_PX = 3.0  # runs whose tops differ by at most this are one visual line
HEADER_MAX_TOP_PX = 60.0  # running header (case number, page number) sits at top ~37-38 px; body starts at 73+


def decode_html_payload(content: bytes | str) -> bytes:
    """`OrzeczeniePlikHtml` answer -> HTML bytes. SnNotFound when the ruling has no HTML file."""
    p = unwrap(content)
    raw = p.get("raw") if isinstance(p, dict) else None
    if not isinstance(raw, str) or not raw:
        raise ValueError("unexpected SN HTML payload (no base64 'raw')")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"unexpected SN HTML payload (invalid base64: {e})") from None


def html_title(html: bytes | str) -> str | None:
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    m = _TITLE_RE.search(text)
    if not m:
        return None
    return _ws(htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) or None


def _superscript(m: re.Match) -> str:
    body = m.group(2)
    if not re.fullmatch(r"\s*\d+\s*", body):
        return m.group(0)  # footnote signs, letters etc. are kept as they are
    return re.sub(r"\d+", lambda d: d.group(0).translate(SUP_DIGITS), body)


def _page_lines(page: str) -> list[tuple[float, str]]:
    runs: list[tuple[float, float, str]] = []
    for style, inner in _TXT_RE.findall(page):
        lm, tm = _LEFT_RE.search(style), _TOP_RE.search(style)
        if not (lm and tm):
            continue
        # spans of one run are concatenated as they are: a word split over two spans stays one word;
        # superscript digits become Unicode superscripts ("art. 804<sup>1</sup>" -> "art. 804¹", as in ELI texts)
        inner = _SUP_SPAN_RE.sub(_superscript, inner)
        text = htmllib.unescape(re.sub(r"<[^>]+>", "", inner)).replace(" ", " ")
        if text.strip():
            runs.append((float(tm.group(1)), float(lm.group(1)), text))
    runs.sort(key=lambda r: (r[0], r[1]))
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for top, left, text in runs:
        if lines and abs(top - lines[-1][0]) <= LINE_TOLERANCE_PX:
            lines[-1][1].append((left, text))
        else:
            lines.append((top, [(left, text)]))
    return [(top, _ws(" ".join(t for _, t in sorted(parts)))) for top, parts in lines]


def _is_running_header(line: str, headers: set[str], page_no: int) -> bool:
    n = str(page_no)
    return line == n or line in headers or any(line in (f"{h} {n}", f"{n} {h}") for h in headers)


def ruling_text(html: bytes | str, case_numbers: list[str] | None = None) -> tuple[str, list[str]]:
    """PDF-to-HTML rendering -> plain text in reading order. Returns (text, flags).

    Each page's positioned runs are sorted top-to-bottom, left-to-right; runs on the same visual line are
    joined with a space; superscript digits are rendered as Unicode superscripts. The running header of
    pages 2+ (case number and/or page number in the top margin) is dropped; page 1 is kept whole. Words
    are not changed (line-end hyphenation such as
    "pra-\\nwa" and letter-spaced emphasis such as "o d d a l i ł" stay as in the source).
    Any other markup falls back to generic HTML-to-text conversion (flagged)."""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    pages = _PAGE_RE.split(text)[1:]
    page_lines = [_page_lines(p) for p in pages]
    if not any(page_lines):
        out = html_to_text(text)
        return "\n".join(ln.strip() for ln in out.split("\n") if ln.strip()), ["text_layout_unrecognised"]
    headers = {_ws(c) for c in (case_numbers or []) if c}
    if title := html_title(text):
        headers.add(title)
    lines: list[str] = []
    for no, pl in enumerate(page_lines, 1):
        for top, line in pl:
            if no > 1 and top < HEADER_MAX_TOP_PX and _is_running_header(line, headers, no):
                continue
            lines.append(line)
    return "\n".join(lines), []


def _has_reasons(text: str) -> bool:
    return any(re.sub(r"\s+", "", ln).lower() == "uzasadnienie" for ln in text.split("\n"))


def parse_sn_ruling(details_json: bytes, html_json: bytes, internal_id: str, *, snapshot_id: str, sha256: str,
                    fetched_at: datetime, details_snapshot_id: str | None = None,
                    ) -> tuple[Judgment, LegalDocument]:
    """Build Judgment + LegalDocument. The text snapshot (OrzeczeniePlikHtml answer) is the document
    snapshot; the details snapshot id is kept in metadata."""
    iid = str(internal_id)
    d = parse_details(details_json)
    html = decode_html_payload(html_json)
    title_in_html = html_title(html)
    text, flags = ruling_text(html, d["case_numbers"])
    if not text:
        flags.append("text_empty")
    elif not _has_reasons(text):
        flags.append("uzasadnienie_not_in_text")  # may be published later (see modified_at) or never written
    if d["id"] != iid:
        flags.append(f"details_id_mismatch:{d['id']}")

    case_numbers = list(d["case_numbers"])
    if not case_numbers:
        if title_in_html:
            case_numbers = signatures(title_in_html) or [title_in_html]
            flags.append("case_numbers_from_html_title")
        else:
            flags.append("case_number_missing")
    elif title_in_html and not any(same_case(title_in_html, c) for c in case_numbers):
        flags.append(f"html_title_differs:{title_in_html}")

    jdate, raw_date = d["judgment_date"], d["judgment_date_raw"]
    if jdate is None:
        flags.append("judgment_date_missing" if not raw_date else f"judgment_date_unparseable:{raw_date}")
    elif jdate > fetched_at.date():
        flags += ["judgment_date_in_future", f"judgment_date_raw:{raw_date}"]
        jdate = None
    elif jdate < MIN_PLAUSIBLE:
        flags += ["judgment_date_implausible", f"judgment_date_raw:{raw_date}"]
        jdate = None

    jtype = doc_type(d["form"])
    if jtype == "UNKNOWN":
        flags.append(f"judgment_type_unmapped:{d['form'] or '-'}")
    court = court_name(d["chambers"])
    doc_id = f"sn:{iid}"
    url = page_url(iid)
    judgment = Judgment(
        document_id=doc_id, source_judgment_id=iid, publisher_id=iid, court_name=court, court_type=COURT_TYPE,
        case_numbers=case_numbers, judgment_date=jdate, judgment_type=jtype, text=text,
        original_url=url, snapshot_id=snapshot_id, data_quality_flags=flags,
    )
    form = d["form"] or jtype
    title = " – ".join([
        form[:1].upper() + form[1:], court, ", ".join(case_numbers) or "(brak sygnatury)",
        jdate.isoformat() if jdate else "data niepewna",
    ])
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.judgment, title=title, original_url=url,
        snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "sn_id": iid,
            "details_url": details_url(iid),
            "html_url": html_url(iid),
            "pdf_url": pdf_url(iid),
            "details_snapshot_id": details_snapshot_id,
            "document_type_pl": d["form"],
            "judgment_date_raw": raw_date,
            "chambers": d["chambers"],
            "division": d["division"],
            "bench_type": d["bench_type"],
            "bench": d["bench"],
            "presiding": d["presiding"],
            "rapporteur": d["rapporteur"],
            "co_rapporteurs": d["co_rapporteurs"],
            "reasons_author": d["reasons_author"],
            "dissent_ruling": d["dissent_ruling"],
            "dissent_reasons": d["dissent_reasons"],
            "modified_at": d["modified_at"],
            "html_title": title_in_html,
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc
