"""Minimal HTML -> plain text conversion (stdlib only) that keeps paragraph breaks."""

from __future__ import annotations

import re
from html.parser import HTMLParser

BLOCK_TAGS = {
    "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table",
    "section", "article", "blockquote", "hr", "dd", "dt", "ul", "ol",
}
SKIP_TAGS = {"script", "style", "head", "title"}
SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


class _TextExtractor(HTMLParser):
    def __init__(self, skip_classes: set[str] | None = None):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.stack: list[tuple[str, bool]] = []
        self.skip_classes = skip_classes or set()

    def handle_starttag(self, tag, attrs):
        cls = set((dict(attrs).get("class") or "").split())
        skip = tag in SKIP_TAGS or bool(cls & self.skip_classes)
        if tag not in ("br", "hr", "img", "meta", "link", "col", "input"):
            self.stack.append((tag, skip))
            if skip:
                self.skip_depth += 1
        if tag in BLOCK_TAGS and not self.skip_depth:
            self.parts.append("\n")
        elif tag == "td" and not self.skip_depth:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK_TAGS and not self.skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        # pop to the matching tag (tolerates unclosed elements)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, skip in self.stack[i:]:
                    if skip:
                        self.skip_depth -= 1
                del self.stack[i:]
                break
        if tag in BLOCK_TAGS and not self.skip_depth:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        # newlines inside HTML text are ordinary whitespace; only block elements break lines
        data = re.sub(r"\s+", " ", data)
        if any(t == "sup" for t, _ in self.stack):
            data = data.strip()
            if data.isdigit():
                data = data.translate(SUP_DIGITS)  # "505<sup>13</sup>" -> "505¹³", as in ELI texts
        self.parts.append(data)


def html_to_text(html: str, *, skip_classes: set[str] | None = None) -> str:
    """Strip tags; each block element (paragraph, row, heading) becomes one line.

    Whitespace inside a paragraph is collapsed (HTML rendering semantics); words are not changed.
    """
    ex = _TextExtractor(skip_classes)
    ex.feed(html)
    ex.close()
    raw = "".join(ex.parts).replace(" ", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
    out: list[str] = []
    for ln in lines:
        if not ln:
            continue
        # a lone list marker ("a)", "(i)", "1.", "–") belongs to the following paragraph
        if out and re.fullmatch(r"\(?[0-9a-z]{1,5}\)|[0-9]{1,3}\.|[–—-]", out[-1]):
            out[-1] = f"{out[-1]} {ln}"
        else:
            out.append(ln)
    return "\n".join(out)
