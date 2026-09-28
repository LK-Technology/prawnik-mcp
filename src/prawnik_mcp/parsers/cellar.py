"""Parser for Cellar (Publications Office) XHTML of an EU act as published in the OJ.

The text is the original publication, NOT a consolidated version: later amendments
are not reflected. Articles are the `div.eli-subdivision#art_N` blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from prawnik_mcp.contracts import canonical_locator
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "cellar-xhtml-0.1.0"


@dataclass
class CellarArticle:
    locator: str  # "art. 9"
    heading: str  # "Artykuł 9 – Prawo do odstąpienia od umowy"
    text: str


@dataclass
class CellarDocument:
    title: str
    oj_reference: str | None  # "L 304/64"
    oj_date: str | None  # "22.11.2011"
    articles: list[CellarArticle]
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


class _DivSplitter(HTMLParser):
    """Collects raw HTML of each top-level `div#art_N` (nested divs included)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks: list[tuple[str, list[str]]] = []
        self.depth = 0  # div depth inside the current article
        self.cur: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cur is None and tag == "div" and re.fullmatch(r"art_\d+[a-z]?", a.get("id") or ""):
            self.cur = []
            self.blocks.append((a["id"], self.cur))
            self.depth = 1
            return
        if self.cur is not None:
            if tag == "div":
                self.depth += 1
            self.cur.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag, attrs):
        if self.cur is not None:
            self.cur.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.cur = None
                return
        self.cur.append(f"</{tag}>")

    def handle_data(self, data):
        if self.cur is not None:
            self.cur.append(data)

    def handle_entityref(self, name):
        if self.cur is not None:
            self.cur.append(f"&{name};")

    def handle_charref(self, name):
        if self.cur is not None:
            self.cur.append(f"&#{name};")


def _first(cls: str, html: str) -> str | None:
    m = re.search(rf'<p[^>]*class="{cls}"[^>]*>(.*?)</p>', html, re.S)
    return html_to_text(m.group(1)) if m else None


def parse_cellar_xhtml(content: bytes) -> CellarDocument:
    html = content.decode("utf-8", errors="replace")
    main = re.search(r'<div class="eli-main-title"[^>]*>(.*?)</div>', html, re.S)
    title = html_to_text(main.group(1)).replace("\n", " ") if main else ""
    title = re.sub(r"\s*\(Tekst mający znaczenie dla EOG\)\s*$", "", title)
    sp = _DivSplitter()
    sp.feed(html)
    sp.close()
    arts: list[CellarArticle] = []
    warnings: list[str] = []
    for div_id, parts in sp.blocks:
        raw = "".join(parts)
        # footnote call-outs "(1)" are links to notes, not part of the article text
        raw = re.sub(r'<a[^>]*>\s*\(\s*<span class="oj-super oj-note-tag">[^<]*</span>\s*\)\s*</a>', "", raw)
        text = html_to_text(raw)
        num = div_id.split("_", 1)[1]
        loc = canonical_locator(f"art. {num}")
        if not loc:
            warnings.append(f"nieparsowalny identyfikator {div_id}")
            continue
        lines = text.split("\n")
        heading = lines[0] if lines else f"Artykuł {num}"
        sub = _first("oj-sti-art", raw)
        arts.append(CellarArticle(locator=loc, heading=f"{heading} – {sub}" if sub else heading, text=text))
    if not arts:
        warnings.append("nie znaleziono artykułów (div#art_N)")
    return CellarDocument(
        title=title or "(brak tytułu)",
        oj_reference=_first("oj-hd-oj", html),
        oj_date=_first("oj-hd-date", html),
        articles=arts,
        warnings=warnings,
    )
