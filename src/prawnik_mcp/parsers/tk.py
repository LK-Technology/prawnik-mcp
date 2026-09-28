"""Parsers for Constitutional Tribunal (Trybunał Konstytucyjny) rulings from trybunal.gov.pl.

The Tribunal's TYPO3 site publishes every ruling as a news article. Four kinds of pages are read
(stdlib regex / html.parser only):

1. ruling – `GET /postepowanie-i-orzeczenia/{wyroki|postanowienia}/art/{slug}`: `article.master-article`
   with the **operative part only** (case number, date, composition, sentencja, voting, dissent markers).
   The reasoning (uzasadnienie) is not published on this site (only on IPO / OTK ZU);
2. case page – `GET /s/{signature}` (the site's official short link, e.g. `/s/sk-20-25`; older cases use
   the compact form `/s/p-3512`): the case with an accordion of related articles, whose "Wyrok (n)" and
   "Postanowienie (n)" groups link to the ruling articles. Unknown signatures answer 404;
3. search – `GET /wyszukiwarka?tx_solr[q]=...` (EXT:solr), 10 hits per page; `data-document-id` carries
   the tt_news uid. Hits have a title and a highlighted snippet, no structured case number or date;
4. listing – `GET /postepowanie-i-orzeczenia/{wyroki|postanowienia}` and its pager links
   (`tx_ttnews[pointer]` + TYPO3 `cHash`, which cannot be computed, so the pager links are followed).
   Newest ruling first; each item reads "K 21/26, 22 IX 2026".

Ruling text is data, never instructions: it is converted to plain text and stored as-is (older articles
contain encoding damage made by the source, e.g. "Sšdu" for "Sądu"; it is flagged, never repaired).
"""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlencode, urlsplit

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text
from prawnik_mcp.parsers.kio import PL_MONTHS, fold  # shared helpers (see parsers/kio.py for provenance)

PARSER_VERSION = "tk-html-0.1.0"
BASE_URL = "https://trybunal.gov.pl"
SEARCH_URL = f"{BASE_URL}/wyszukiwarka"
IPO_BASE = "https://ipo.trybunal.gov.pl"
COURT_NAME = "Trybunał Konstytucyjny"
COURT_TYPE = "CONSTITUTIONAL_TRIBUNAL"  # same value SAOS uses for TK
SEARCH_PAGE_SIZE = 10  # fixed by the site
MIN_PLAUSIBLE = date(1986, 1, 1)  # the Tribunal started work in 1986
FINALITY_BASIS = "art. 190 ust. 1 Konstytucji RP (orzeczenia TK są ostateczne)"

# URL section -> judgment_type (SAOS vocabulary, so TK from SAOS and from trybunal.gov.pl agree)
SECTIONS = {"wyroki": "SENTENCE", "postanowienia": "DECISION"}
SECTION_CATEGORY = {"wyroki": "Wyrok", "postanowienia": "Postanowienie"}  # Solr `category` facet values
CATEGORY_SECTION = {"wyrok": "wyroki", "postanowienie": "postanowienia"}
DOC_TYPES_PL = {"SENTENCE": "Wyrok", "DECISION": "Postanowienie"}
TEXT_SCOPE = "operative_part_only"

ROMAN_MONTHS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
                "XI": 11, "XII": 12}

# TK case-number prefixes: K, Kp, Kpt, P, Pp, U, SK, M (site's "Typy sygnatur"), Ts/Tw (preliminary review),
# S (signalling decisions) and W (statutory interpretation, before 1997).
TK_PREFIXES = ("SK", "Kpt", "Kp", "K", "Pp", "P", "U", "M", "Ts", "Tw", "S", "W")
_PREFIX_CANON = {p.lower(): p for p in TK_PREFIXES}
# Common-court and SN repertories share letters with TK prefixes ("II K 123/24", "IV P 7/20", "III U 1/21"):
# a TK number is never preceded by a Roman-numeral division. KIO/OSK/GSK etc. are excluded by the
# "not glued to a word" lookbehind.
_NOT_AFTER_ROMAN = "".join(rf"(?<!\b[IVXLC]{{{n}}}\s)" for n in range(1, 6))
TK_CASE_PATTERN = (r"(?<![\w/.-])" + _NOT_AFTER_ROMAN
                   + r"(?:SK|Kpt|Kp|K|Pp|P|U|M|Ts|Tw|S|W)\s?\d{1,4}\s*/\s*\d{2}(?![\w/])")
TK_CASE_RE = re.compile(TK_CASE_PATTERN)
_SIG_PARTS_RE = re.compile(r"(?<![\w/.-])" + _NOT_AFTER_ROMAN
                           + r"(SK|Kpt|Kp|K|Pp|P|U|M|Ts|Tw|S|W)\s?(\d{1,4})\s*/\s*(\d{2})(?![\w/])")
_SIG_PARTS_CI_RE = re.compile(_SIG_PARTS_RE.pattern, re.IGNORECASE)  # user input ("sk 12/19")

ID_RE = re.compile(r"(wyroki|postanowienia)/([a-z0-9][a-z0-9-]{0,299})")
_RULING_PATH_RE = re.compile(r"/postepowanie-i-orzeczenia/(wyroki|postanowienia)/art/([a-z0-9][a-z0-9-]{0,299})/?$")
_UID_FROM_SLUG_RE = re.compile(r"^(\d{1,7})-")
_RULING_DATE_RE = re.compile(r"\bdnia\s+(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})\s*r?\.?", re.IGNORECASE)
_ROMAN_DATE_RE = re.compile(r"\b(\d{1,2})\s+([IVX]{1,4})\s+(\d{4})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_OTK_RE = re.compile(r"\bOTK\s+ZU\s+(?:nr\s+)?[0-9A-Za-z/]{1,20},?\s+poz\.\s*\d{1,4}")
# windows-1250 text the source stored as ISO-8859-2 / Latin-1: "ą" -> "š", "Ą" -> "Ľ", and "ś", "ź", "–"
# become C1 control characters (U+0080-U+009F)
_MOJIBAKE_RE = re.compile("[šĽ\u0080-\u009f]")
_CP1250_REPAIR = {i: bytes([i]).decode("cp1250", errors="ignore") for i in range(0x80, 0xA0)} | {
    ord("š"): "ą", ord("Ľ"): "Ą"}
_GROUP_RE = re.compile(r"<h3>\s*([^<(]+?)\s*\((\d+)\)\s*<span", re.S)


# --------------------------------------------------------------------------- ids and URLs


def ruling_url(section: str, slug: str) -> str:
    return f"{BASE_URL}/postepowanie-i-orzeczenia/{section}/art/{slug}"


def listing_url(section: str) -> str:
    return f"{BASE_URL}/postepowanie-i-orzeczenia/{section}"


def doc_id(section: str, slug: str) -> str:
    return f"tk:{section}/{slug}"


def path_to_id(path_or_url: str) -> tuple[str, str] | None:
    """'/postepowanie-i-orzeczenia/wyroki/art/<slug>' (or a full URL) -> ('wyroki', '<slug>')."""
    m = _RULING_PATH_RE.search(urlsplit(htmllib.unescape(path_or_url)).path)
    return (m.group(1), m.group(2)) if m else None


def uid_from_slug(slug: str) -> str | None:
    """Older slugs start with the tt_news uid ('11300-planowanie-rodziny-...'); newer ones do not."""
    m = _UID_FROM_SLUG_RE.match(slug)
    return m.group(1) if m else None


def search_url(query: str, *, sections: tuple[str, ...] = tuple(SECTIONS), page: int = 1) -> str:
    """Solr site search restricted to ruling articles. Values of the same facet are ORed by the site
    (verified 2026-09-28: Wyrok 38 + Postanowienie 31 = 69 hits)."""
    params: list[tuple[str, str]] = [("tx_solr[q]", query)]
    for i, s in enumerate(sections):
        params.append((f"tx_solr[filter][{i}]", f"category:{SECTION_CATEGORY[s]}"))
    if page > 1:
        params.append(("tx_solr[page]", str(page)))
    return f"{SEARCH_URL}?{urlencode(params)}"


def _abs(href: str) -> str:
    href = htmllib.unescape(href)
    return href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"


def _https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


# --------------------------------------------------------------------------- helpers


def _plain(fragment: str) -> str:
    """Inline HTML fragment -> one line of text."""
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def repair_cp1250(text: str) -> str:
    """Undo the source's windows-1250 damage ('Cie\\x9clak' -> 'Cieślak', 'Sšdu' -> 'Sądu').

    Used only for derived metadata (names, subject); the stored ruling text is never altered."""
    return text.translate(_CP1250_REPAIR)


def _canon(prefix: str, num: str, year: str) -> str:
    return f"{_PREFIX_CANON[prefix.lower()]} {int(num)}/{year}"


def normalize_signature(raw: str) -> str | None:
    """User input -> canonical TK case number ('sk 12/19' -> 'SK 12/19'); None if it is not one."""
    m = _SIG_PARTS_CI_RE.search((raw or "").strip())
    return _canon(*m.groups()) if m else None


def as_signature(text: str) -> str | None:
    """The whole string is one TK case number ('K 1/20', 'sk 12/19') -> canonical form, else None."""
    m = _SIG_PARTS_CI_RE.fullmatch((text or "").strip())
    return _canon(*m.groups()) if m else None


def signatures(raw: str) -> list[str]:
    """All TK case numbers in a text fragment (joined cases list several)."""
    out: list[str] = []
    for m in _SIG_PARTS_RE.finditer(raw or ""):
        s = _canon(*m.groups())
        if s not in out:
            out.append(s)
    return out


def case_slugs(signature: str) -> list[str]:
    """Short-link slugs to try for a case: current form 'sk-20-25', then the older compact 'p-3512'."""
    m = _SIG_PARTS_CI_RE.fullmatch(signature.strip())
    if not m:
        return []
    p, n, y = m.group(1).lower(), int(m.group(2)), m.group(3)
    return [f"{p}-{n}-{y}", f"{p}-{n}{y}"]


def case_url(slug: str) -> str:
    return f"{BASE_URL}/s/{slug}"


def parse_pl_date(text: str | None) -> date | None:
    """'23 czerwca 2026', '22 IX 2026' or '2026-06-23'. Garbage -> None."""
    if not text:
        return None
    m = _ISO_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _ROMAN_DATE_RE.search(text)
    if m and m.group(2) in ROMAN_MONTHS:
        try:
            return date(int(m.group(3)), ROMAN_MONTHS[m.group(2)], int(m.group(1)))
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


# --------------------------------------------------------------------------- related-article accordion


@dataclass
class TkLink:
    """One entry of a listing, a case-page accordion or a search result."""

    path: str
    section: str | None  # "wyroki" | "postanowienia" for ruling articles, else None
    slug: str | None
    case_numbers: list[str]
    date: date | None
    date_raw: str | None
    title: str = ""
    snippet: str = ""
    uid: str | None = None
    group: str | None = None  # accordion group label ("Wyrok", "Komunikat po", ...)

    @property
    def document_id(self) -> str | None:
        return doc_id(self.section, self.slug) if self.section and self.slug else None

    def listing(self) -> dict:
        """Listing metadata kept with the stored ruling (cross-checks date and case number)."""
        return {"case_numbers": self.case_numbers, "date": self.date.isoformat() if self.date else None,
                "date_raw": self.date_raw, "uid": self.uid, "title": self.title or None}


def _link(path: str, **kw) -> TkLink:
    sid = path_to_id(path)
    section, slug = sid if sid else (None, None)
    uid = kw.pop("uid", None) or (uid_from_slug(slug) if slug else None)
    return TkLink(path=htmllib.unescape(path), section=section, slug=slug, uid=uid, **kw)


def related_groups(page_html: str) -> dict[str, list[TkLink]]:
    """The 'documents-accordion' of a ruling or case page -> {group label: [links]}.

    Group labels as shown by the site: "Sprawy w Trybunale", "Rozprawy", "Publiczne ogłoszenie orzeczenia",
    "Komunikat przed", "Wyrok", "Postanowienie", "Komunikat po", "Pozostałe"."""
    start = page_html.find('class="documents-accordion')
    if start < 0:
        return {}
    block = page_html[start:]
    stop = min((i for i in (block.find('class="link-switch-wrap"'), block.find("<!--/ article-footer-->")) if i > 0),
               default=len(block))
    block = block[:stop]
    heads = list(_GROUP_RE.finditer(block))
    out: dict[str, list[TkLink]] = {}
    for i, h in enumerate(heads):
        seg = block[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(block)]
        label = _plain(h.group(1))
        links: list[TkLink] = []
        for a in re.finditer(r'<li\b[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', seg, re.S):
            inner = a.group(2)
            sub = re.search(r'<div class="widget-subtitle">(.*?)</div>', inner, re.S)
            sub_text = _plain(sub.group(1)) if sub else ""
            sig_part, _, date_part = sub_text.partition("|")
            metas = [_plain(x) for x in re.findall(r'<p class="meta"\s*>(.*?)</p>', inner, re.S)]
            links.append(_link(a.group(1), case_numbers=signatures(sig_part), date=parse_pl_date(date_part),
                               date_raw=date_part.strip() or None, title=_plain(_attr(a.group(0), "title") or ""),
                               snippet=metas[-1] if metas else "", group=label))
        out[label] = links
    return out


def _attr(tag_html: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', tag_html)
    return htmllib.unescape(m.group(1)) if m else None


# --------------------------------------------------------------------------- case page (/s/<sig>)


@dataclass
class TkCasePage:
    case_numbers: list[str]
    subject: str
    rulings: list[TkLink]  # links from the "Wyrok" and "Postanowienie" groups
    groups: dict[str, list[TkLink]] = field(default_factory=dict)
    canonical: str | None = None


def parse_case_page(content: bytes | str) -> TkCasePage:
    """Parse `/s/{signature}`. Raises ValueError when the page has no case article or accordion."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    art_start = text.find('<article class="master-article')
    if art_start < 0 or 'class="documents-accordion' not in text:
        raise ValueError("unexpected TK case page (no case article or related-documents accordion)")
    h1 = re.search(r'<h1 class="article-title">(.*?)</h1>', text[art_start:], re.S)
    em = re.search(r'<em class="signature-number">(.*?)</em>', h1.group(1), re.S) if h1 else None
    subject = _plain(re.sub(r'<em class="signature-number">.*?</em>', "", h1.group(1), flags=re.S)) if h1 else ""
    groups = related_groups(text)
    rulings = [lk for label in ("Wyrok", "Postanowienie") for lk in groups.get(label, []) if lk.section]
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', text)
    return TkCasePage(case_numbers=signatures(_plain(em.group(1))) if em else [], subject=subject,
                      rulings=rulings, groups=groups, canonical=canon.group(1) if canon else None)


# --------------------------------------------------------------------------- listing pages


@dataclass
class TkListingPage:
    section: str
    items: list[TkLink]
    current_page: int
    last_page: int
    next_url: str | None  # absolute URL of the next page (pager link with cHash), None on the last page


def parse_listing(content: bytes | str, section: str) -> TkListingPage:
    """Parse `/postepowanie-i-orzeczenia/{wyroki|postanowienia}` (or a pager page). Raises ValueError
    when the article list is missing (a layout change, not "no rulings")."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    if 'class="article-list-section"' not in text:
        raise ValueError("unexpected TK listing page (no article-list-section)")
    items: list[TkLink] = []
    for m in re.finditer(r'<li class="article-item[^"]*">\s*<a href="([^"]+)"([^>]*)>\s*<p class="meta">(.*?)</p>'
                         r'\s*<h2>(.*?)</h2>', text, re.S):
        meta = _plain(m.group(3))
        sig_part, _, date_part = meta.rpartition(",")
        lk = _link(m.group(1), case_numbers=signatures(sig_part or meta), date=parse_pl_date(date_part),
                   date_raw=date_part.strip() or None, title=_plain(m.group(4)))
        if lk.section:
            items.append(lk)
    pager = re.search(r'<nav class="article-pagination">(.*?)</nav>', text, re.S)
    current, last, nxt = 1, 1, None
    if pager:
        p = pager.group(1)
        cur = re.search(r'<li class="current"><a [^>]*>(\d+)</a>', p)
        current = int(cur.group(1)) if cur else 1
        nums = [int(x) for x in re.findall(r"<a [^>]*>(\d+)</a>", p)]
        last = max(nums + [current])
        n = re.search(r'<li class="next"><a href="([^"]+)"', p)
        if n and current < last:
            nxt = _abs(n.group(1))
    return TkListingPage(section=section, items=items, current_page=current, last_page=last, next_url=nxt)


# --------------------------------------------------------------------------- Solr search results


@dataclass
class TkSearchPage:
    total: int
    items: list[TkLink]

    @property
    def pages(self) -> int:
        return -(-self.total // SEARCH_PAGE_SIZE)


def parse_search(content: bytes | str) -> TkSearchPage:
    """Parse `/wyszukiwarka?tx_solr[q]=...`. Raises ValueError if the Solr result block is missing
    (a 200 without it is a layout change, not "0 hits"; real zero hits read 'Wyniki dla "..." (0)')."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    if 'id="tx-solr-search"' not in text:
        raise ValueError("unexpected TK search response (no tx-solr-search block)")
    items: list[TkLink] = []
    for m in re.finditer(r'<li class="article-item">(.*?)</li>', text, re.S):
        chunk = m.group(1)
        a = re.search(r"<a\b[^>]*>", chunk)
        if not a:
            continue
        url = _attr(a.group(0), "data-document-url") or _attr(a.group(0), "href") or ""
        uid = re.search(r"/tt_news/(\d+)", _attr(a.group(0), "data-document-id") or "")
        title = re.search(r"<h2>(.*?)</h2>", chunk, re.S)
        body = chunk[title.end():] if title else chunk
        body = re.split(r'<p class="article-meta"', body, maxsplit=1)[0]
        snippet = _plain(body)
        d = _RULING_DATE_RE.search(snippet)
        lk = _link(url, case_numbers=signatures(snippet[:200]) if "Sygn" in snippet[:40] else [],
                   date=parse_pl_date(d.group(0)) if d and "Sygn" in snippet[:40] else None,
                   date_raw=d.group(0) if d and "Sygn" in snippet[:40] else None,
                   title=_plain(title.group(1)) if title else "", snippet=snippet, uid=uid.group(1) if uid else None)
        if lk.section and all(x.path != lk.path for x in items):
            items.append(lk)
    total = len(items)
    t = re.search(r"Wyniki dla\s*<em>.*?</em>\s*\((\d+)\)", text, re.S)
    if t:
        total = int(t.group(1))
    return TkSearchPage(total=total, items=items)


# --------------------------------------------------------------------------- ruling page


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _is_marker(line: str, word: str) -> bool:
    """'o r z e k a :' / 'orzeka:' / 'Ponadto p o s t a n a w i a:' -> True for word 'orzeka'/'postanawia'."""
    squashed = re.sub(r"\s+", "", fold(line)).lower().rstrip(":")
    return squashed in (word, f"ponadto{word}")


def _composition(lines: list[str]) -> list[dict]:
    idx = next((i for i, ln in enumerate(lines) if re.search(r"w\s+sk[lł]adzie\s*:?\s*$", fold(ln), re.I)), None)
    if idx is None:
        return []
    out: list[dict] = []
    for ln in (repair_cp1250(x) for x in lines[idx + 1: idx + 17]):
        f = fold(ln).lower()
        if (f.startswith(("protokolant", "po rozpoznaniu", "w sprawie", "przy udziale"))
                or _is_marker(ln, "orzeka") or _is_marker(ln, "postanawia") or len(ln) > 90 or re.search(r"\d", ln)):
            break
        ln = ln.rstrip(" ,.;")
        # "Name – przewodniczący", "Name – przewodniczący, II sprawozdawca", "Name – I sprawozdawca";
        # older pages lost the dash ("Name  przewodniczący"), hence the second pattern
        m = re.match(r"^(.+?)\s+[–—-]\s+(.+)$", ln) or re.match(
            r"^(.+?)\s+((?:[IV]{1,3}\s+)?(?:przewodnicz|sprawozdaw)\w*.*)$", ln)
        name, role = (m.group(1).strip(), m.group(2).strip()) if m else (ln, None)
        if name:
            out.append({"name": name, "role": role})
    return out


def _operative_part(lines: list[str]) -> str | None:
    start = next((i for i, ln in enumerate(lines) if _is_marker(ln, "orzeka") or _is_marker(ln, "postanawia")), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if fold(lines[i]).lower().startswith("orzeczenie zapadlo")),
               len(lines))
    return "\n".join(lines[start:end])[:4000] or None


def parse_ruling_page(content: bytes | str) -> dict:
    """Parse a ruling article. Raises ValueError when the article block or its title is missing."""
    page = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    start = page.find('<article class="master-article')
    if start < 0:
        raise ValueError("unexpected TK ruling page (no master-article block)")
    end = page.find("</article>", start)
    art = page[start: end if end > 0 else len(page)]
    h1 = re.search(r'<h1 class="article-title">(.*?)</h1>', art, re.S)
    if not h1:
        raise ValueError("unexpected TK ruling page (no article title)")
    cat = re.search(r'<span class="typo-search-like-css">(.*?)</span>', art, re.S)
    em = re.search(r'<em class="signature-number">(.*?)</em>', h1.group(1), re.S)
    subject = _plain(re.sub(r'<em class="signature-number">.*?</em>', "", h1.group(1), flags=re.S))
    text = "\n".join(_lines(html_to_text(art[h1.end():])))
    lines = _lines(text)
    tail = page[end:] if end > 0 else ""

    head_end = next((i for i, ln in enumerate(lines) if re.search(r"w\s+sk[lł]adzie", fold(ln), re.I)), min(len(lines), 8))
    header = "\n".join(lines[:head_end])
    dm = _RULING_DATE_RE.search(header)
    # "Leon Kieres (votum separatum)", "Wojciech Hermeliński (zdanie odrębne ad. I pkt 3, 5, 6 wyroku ...)"
    diss = [{"name": m.group(1).strip(" ,"), "note": re.sub(r"\s+", " ", m.group(2)).strip()} for ln in lines
            if (m := re.match(r"^(.+?)\s*\(((?:zdanie odrębne|votum separatum)\b[^)]*)\)", repair_cp1250(ln), re.I))]
    sep_idx = next((i for i, ln in enumerate(lines) if re.match(r"zdani[ea]\s+odrebn\w*\s+do\s+uzasadnienia",
                                                                 fold(ln), re.I)), None)
    sep = [repair_cp1250(ln).rstrip(" ,.") for ln in lines[sep_idx + 1:] if len(ln) <= 60 and not re.search(r"\d", ln)] \
        if sep_idx is not None else []
    low = fold(text).lower()
    unanimous = True if "zapadlo jednoglosnie" in low else False if "zapadlo wiekszoscia glosow" in low else None
    heading = next((ln for ln in lines[:6] if re.sub(r"\s+", "", ln).upper() in {"WYROK", "POSTANOWIENIE"}
                    or re.sub(r"\s+", "", ln).upper().startswith(("WYROK", "POSTANOWIENIE"))), "")
    heading_type = ("SENTENCE" if re.sub(r"\s+", "", heading).upper().startswith("WYROK")
                    else "DECISION" if heading else None)
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', page)
    published = re.search(r'<time datetime="(\d{4}-\d{2}-\d{2})"', tail)
    ipo = re.search(r'href="(https?://ipo\.trybunal\.gov\.pl/[^"]+)"', tail)
    short = re.search(r'href="(/s/[^"]+)"', tail)
    groups = related_groups(tail)
    press = next((lk for lk in groups.get("Komunikat po", [])), None)
    otk = _OTK_RE.search(text)
    composition = _composition(lines)
    return {
        "category": _plain(cat.group(1)) if cat else None,
        "heading_type": heading_type,
        "subject": repair_cp1250(subject) or None,
        "signature_field": _plain(em.group(1)) if em else None,
        "case_numbers": signatures(_plain(em.group(1))) if em else [],
        "text": text,
        "judgment_date": parse_pl_date(dm.group(0)) if dm else None,
        "judgment_date_raw": dm.group(0).strip() if dm else None,
        "composition": composition,
        "chair": next((c["name"] for c in composition if c["role"] and "przewodnicz" in c["role"]), None),
        "rapporteurs": [c["name"] for c in composition if c["role"] and "sprawozdaw" in c["role"]],
        "dissenting_opinions": diss,
        "separate_opinions_to_reasoning": sep,
        "unanimous": unanimous,
        "operative_part": _operative_part(lines),
        "otk_reference": re.sub(r"\s+", " ", otk.group(0)) if otk else None,
        "canonical_url": canonical.group(1) if canonical else None,
        "published_on_site": published.group(1) if published else None,
        "ipo_case_url": _https(htmllib.unescape(ipo.group(1))) if ipo else None,
        "case_url": _abs(short.group(1)) if short else None,
        "press_release_url": _abs(press.path) if press else None,
        "related_groups": {k: len(v) for k, v in groups.items()},
    }


def parse_tk_ruling(content: bytes, section: str, slug: str, *, snapshot_id: str, sha256: str,
                    fetched_at: datetime, listing: dict | None = None) -> tuple[Judgment, LegalDocument]:
    """Build Judgment + LegalDocument from a ruling article. `listing` (from a listing, case page or
    search hit) is used only to cross-check or, when the page has no date, as a flagged fallback."""
    d = parse_ruling_page(content)
    listing = listing or {}
    text = d["text"]
    flags: list[str] = ["reasoning_not_included"]  # trybunal.gov.pl publishes the operative part only
    if not text:
        flags.append("text_empty")
    if _MOJIBAKE_RE.search(text):
        flags.append("source_encoding_artifacts")

    case_numbers = list(d["case_numbers"])
    if not case_numbers:
        found = signatures(text[:400])
        if found:
            case_numbers = found
            flags.append("case_numbers_from_text")
        elif listing.get("case_numbers"):
            case_numbers = list(listing["case_numbers"])
            flags.append("case_numbers_from_listing")
        else:
            flags.append("case_number_missing")

    jdate, raw_date = d["judgment_date"], d["judgment_date_raw"]
    listed = date.fromisoformat(listing["date"]) if listing.get("date") else None
    if jdate is None:
        if listed:
            jdate = listed
            flags.append("judgment_date_from_listing")
        else:
            flags.append("judgment_date_missing")
    elif listed and listed != jdate:
        flags.append(f"judgment_date_mismatch_listing:{listed.isoformat()}")
    if jdate and jdate > fetched_at.date():
        flags += ["judgment_date_in_future", f"judgment_date_raw:{raw_date}"]
        jdate = None
    elif jdate and jdate < MIN_PLAUSIBLE:
        flags += ["judgment_date_implausible", f"judgment_date_raw:{raw_date}"]
        jdate = None

    jtype = SECTIONS[section]
    cat_section = CATEGORY_SECTION.get(fold(d["category"] or "").lower())
    if cat_section and cat_section != section:
        flags.append(f"category_mismatch:{d['category']}")
    if d["heading_type"] and d["heading_type"] != jtype:
        flags.append(f"heading_type_mismatch:{d['heading_type']}")
    canonical = d["canonical_url"]
    if canonical and path_to_id(canonical) not in (None, (section, slug)):
        flags.append(f"canonical_mismatch:{canonical}")

    uid = listing.get("uid") or uid_from_slug(slug)
    did = doc_id(section, slug)
    url = ruling_url(section, slug)
    judgment = Judgment(
        document_id=did, source_judgment_id=f"{section}/{slug}", publisher_id=uid, court_name=COURT_NAME,
        court_type=COURT_TYPE, case_numbers=case_numbers, judgment_date=jdate, judgment_type=jtype, text=text,
        finality="final", original_url=url, snapshot_id=snapshot_id, data_quality_flags=flags,
    )
    title = " – ".join([
        DOC_TYPES_PL[jtype], COURT_NAME, ", ".join(case_numbers) or "(brak sygnatury)",
        jdate.isoformat() if jdate else "data niepewna",
    ])
    doc = LegalDocument(
        document_id=did, kind=SourceKind.judgment, title=title, original_url=url,
        snapshot_id=snapshot_id, sha256=sha256,
        publication=d["otk_reference"],
        metadata={
            "tk_section": section,
            "tt_news_uid": uid,
            "subject": d["subject"],
            "document_type_pl": d["category"],
            "judgment_date_raw": raw_date,
            "published_on_site": d["published_on_site"],
            "composition": d["composition"],
            "chair": d["chair"],
            "rapporteurs": d["rapporteurs"],
            "dissenting_opinions": d["dissenting_opinions"],
            "separate_opinions_to_reasoning": d["separate_opinions_to_reasoning"],
            "unanimous": d["unanimous"],
            "operative_part": d["operative_part"],
            "otk_reference": d["otk_reference"],
            "case_url": d["case_url"],
            "ipo_case_url": d["ipo_case_url"],
            "press_release_url": d["press_release_url"],
            "text_scope": TEXT_SCOPE,
            "finality_basis": FINALITY_BASIS,
            "listing": listing or None,
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc
