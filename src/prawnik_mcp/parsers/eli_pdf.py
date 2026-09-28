"""Parser for ELI consolidated-text PDFs (obwieszczenie Marszałka Sejmu + załącznik).

Works on the text layer produced by pypdf, annotated with font size and baseline
(pypdf `visitor_text`). This lets us:
- restore superscript article numbers (pypdf flattens "Art. 22¹" to "Art. 221" or
  "Art. 22[1]"): small, raised digits directly after text are superscripts;
- drop footnote references ("konsumenta¹⁾") and footnote bodies (smaller font at the
  page bottom), keeping footnotes aside in `footnotes`;
- drop running page headers "Dziennik Ustaw – N – Poz. X".

Text is otherwise kept as printed: we only collapse repeated spaces, join lines of
one paragraph and join words hyphenated at a line break when the unhyphenated form
is not contradicted by the document itself.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from pypdf import PdfReader

from prawnik_mcp.contracts import canonical_locator

PARSER_VERSION = "eli-pdf-0.1.0"

SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
SUP_CLASS = "⁰¹²³⁴-⁹"
UNSUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}
_DATE = r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\s*r\."

_PAGE_HEADER = re.compile(r"^\s*Dziennik\s+Ustaw\s*[–-]\s*\d+\s*[–-]\s*Poz\.\s*\d+\s*$")
_ART_HEAD = re.compile(
    rf"^Art\.\s*(?P<num>\d+)(?P<letter>[a-z]{{0,3}})(?P<sup>[{SUP_CLASS}]+|\[\d+\])?\.(?=\s|$)"
)
_STRUCT_HEAD = re.compile(
    r"^(?:KSIĘGA\s+\w+|CZĘŚĆ\s+\w+|TYTUŁ\s+[IVXLC]+\w*|DZIAŁ\s+[IVXLC]+\w*|Rozdział\s+\d+\w*|Oddział\s+\d+\w*)\b"
)
_ANNEX_HEAD = re.compile(r"^Załącznik\s+nr\s+(\d+)\s*$")
_ANNEXES_BLOCK = re.compile(r"^Załączniki\s+do\s+ustawy")
_UNIT_START = re.compile(rf"^(?:§\s*\d+[a-z]*[{SUP_CLASS}]*\.|\d{{1,3}}[a-z]{{0,2}}[{SUP_CLASS}]*\.\s|\d+[a-z]*[{SUP_CLASS}]*\)|[a-z]\)|–\s|Art\.\s*\d)")


def parse_polish_date(day: str, month: str, year: str) -> date | None:
    m = MONTHS.get(month.lower())
    try:
        return date(int(year), m, int(day)) if m else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- data


@dataclass
class ParsedArticle:
    locator: str  # canonical, e.g. "art. 22^1"
    heading: str  # as printed, e.g. "Art. 22¹."
    text: str
    pages: list[int]
    footnote_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def page_hint(self) -> str:
        if not self.pages:
            return ""
        a, b = min(self.pages), max(self.pages)
        return f"s. {a}" if a == b else f"s. {a}–{b}"


@dataclass
class IncludedAmendment:
    eli_id: str  # "DU/2025/1172"
    description: str  # as printed


@dataclass
class ObwieszczenieHeader:
    publication: str | None  # "Dz.U. 2026 poz. 1244"
    publication_date: date | None  # "Warszawa, dnia ..."
    announcement_date: date | None  # "z dnia ..." of the obwieszczenie
    state_date: date | None  # "z uwzględnieniem stanu prawnego na dzień"
    base_text: str | None  # previous TJ referenced, e.g. "Dz.U. 2024 poz. 1796"
    included_amendments: list[IncludedAmendment]
    excluded_provisions: list[str]  # faithful quotes from point 2
    later_entry_dates: list[tuple[str, date]]  # (eli_id, date) found in quoted entry-into-force rules
    text: str  # whole header text (with restored superscripts)


@dataclass
class ConsolidatedText:
    header: ObwieszczenieHeader
    articles: list[ParsedArticle]
    annexes: list[ParsedArticle]
    footnotes: dict[str, str]
    warnings: list[str]
    page_count: int
    parser_version: str = PARSER_VERSION


# --------------------------------------------------------------------------- text layer


@dataclass
class _Line:
    text: str
    page: int
    footnote_refs: list[str]


class _PageCollector:
    """Collects annotated pypdf chunks of one page into body / aux streams."""

    def __init__(self, body_size: float):
        self.body_size = body_size
        self.body: list[str] = []
        self.aux: list[str] = []
        self.stream = "body"
        self.base_y: float | None = None
        self.sup: list[str] = []
        self.sup_ws = ""
        self.in_marker = False
        self.footnote_refs: list[tuple[int, str]] = []  # (position in body text, number)
        self.annex_marker = False  # "Załącznik do obwieszczenia" seen on this page
        self.annex_marker_pos: int | None = None
        self.warnings: list[str] = []

    def _flush_sup(self) -> None:
        if not self.sup:
            return
        raw = "".join(self.sup).strip()
        ws, self.sup, self.sup_ws = self.sup_ws, [], ""
        target = self.body if self.stream == "body" else self.aux
        inner = raw.strip("[]")
        if re.fullmatch(r"\d+\)|\*+\)?", raw):
            if self.stream == "body":
                self.footnote_refs.append((sum(len(s) for s in self.body), raw.rstrip(")")))
        elif inner.isdigit():
            target.append(inner.translate(SUP_DIGITS))
        elif raw in ("(*)", "*"):
            target.append(raw)  # printed asterisk marker of a form (e.g. withdrawal form)
        elif raw:
            self.warnings.append(f"nierozpoznany indeks górny {raw!r}")
            target.append(raw)
        if ws:
            target.append(ws)

    def visit(self, text: str, cm, tm, font_dict, font_size) -> None:
        if not text:
            return
        scale = abs(tm[3] * cm[3]) or 1.0
        size = float(font_size) * scale
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        if not text.strip():
            if self.sup and "\n" not in text:
                self.sup_ws += text
                return
            self._flush_sup()
            (self.body if self.stream == "body" else self.aux).append(text)
            return
        small = size < 0.8 * self.body_size
        if small:
            raised = (
                self.base_y is not None and 0.5 < (y - self.base_y) < self.body_size
            )
            if raised:
                self.sup.append(text)
                return
            # small but on its own baseline: footnote number at the start of a footnote
            self._flush_sup()
            if text.strip() == "(*)" and self.stream == "body":
                self.body.append("\n(*)")  # legend of a form's asterisk markers
                return
            self.stream = "aux"
            # \x01 marks the start of a footnote body; the marker may come in pieces ("2", ")")
            self.aux.append(text if self.in_marker else "\n\x01" + text)
            self.in_marker = True
            return
        self.in_marker = False
        self._flush_sup()
        self.base_y = y
        if size < self.body_size - 0.4:
            if re.match(r"\s*Załącznik\s+nr\s+\d+", text):
                # annex heading of the act printed in a smaller font: keep it as a body line
                self.stream = "body"
                self.body.append("\n" + text.strip() + "\n")
                return
            self.stream = "aux"
            self.aux.append(text)
            if "Załącznik" in text and not self.annex_marker:
                self.annex_marker = True
                self.annex_marker_pos = sum(len(s) for s in self.body)
            return
        self.stream = "body"
        self.body.append(text)

    def finish(self) -> None:
        self._flush_sup()


def _body_font_size(reader: PdfReader, sample_pages: int = 4) -> float:
    sizes: Counter[float] = Counter()
    for page in reader.pages[1 : 1 + sample_pages] or reader.pages[:1]:
        def v(text, cm, tm, fd, fs):
            if text.strip():
                sizes[round(float(fs) * (abs(tm[3] * cm[3]) or 1.0), 2)] += len(text)
        page.extract_text(visitor_text=v)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _split_lines(text: str, page: int, refs: list[tuple[int, str]]) -> list[_Line]:
    lines: list[_Line] = []
    pos = 0
    for raw in text.split("\n"):
        end = pos + len(raw)
        line_refs = [n for (p, n) in refs if pos <= p <= end]
        pos = end + 1
        t = re.sub(r"[ \t ]+", " ", raw).strip()
        if not t or _PAGE_HEADER.match(t):
            continue
        lines.append(_Line(t, page, line_refs))
    return lines


def _parse_footnotes(aux_text: str) -> dict[str, str]:
    notes: dict[str, str] = {}
    for piece in aux_text.split("\x01")[1:]:
        piece = piece.replace("\x01", "")
        m = re.match(r"^\s*(\d+|\*+)\s*\)\s*(.*)$", piece, re.S)
        if not m:
            continue
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in m.group(2).split("\n")]
        notes[m.group(1)] = _join_lines([ln for ln in lines if ln], set()).replace("\n", " ")
    return notes


# --------------------------------------------------------------------------- joining


def _join(prev: str, nxt: str, vocab: set[str], newline: bool = False) -> str:
    m = re.search(r"(\w+)-$", prev)
    n = re.match(r"^(\w+)", nxt)
    if m and n and m.group(1)[-1].islower() and n.group(1)[0].islower():
        hyphenated = f"{m.group(1)}-{n.group(1)}".lower()
        # keep a real compound ("społeczno-gospodarczy") if the document prints it that way inline
        if hyphenated not in vocab:
            return prev[:-1] + nxt
        return prev + nxt
    return prev + ("\n" if newline else " ") + nxt


def _join_lines(lines: list[str], vocab: set[str]) -> str:
    out = ""
    for ln in lines:
        if not out:
            out = ln
            continue
        out = _join(out, ln, vocab, newline=bool(_UNIT_START.match(ln)))
    return out


def _build_vocab(lines: list[_Line]) -> set[str]:
    vocab: set[str] = set()
    for ln in lines:
        # hyphenated tokens that are not at the line end are real compounds
        for w in re.findall(r"\b\w+-\w+\b", ln.text):
            vocab.add(w.lower())
    return vocab


# --------------------------------------------------------------------------- header


def _parse_header(text: str) -> ObwieszczenieHeader:
    text = re.sub(r"([a-ząćęłńóśźż])-[ \t]*\n\s*([a-ząćęłńóśźż])", r"\1\2", text)
    flat = re.sub(r"\s+", " ", text)
    pub = re.search(r"Poz\.\s*(\d+)", flat)
    wd = re.search(r"Warszawa,\s*dnia\s+" + _DATE, flat)
    pub_date = parse_polish_date(*wd.groups()) if wd else None
    publication = f"Dz.U. {pub_date.year} poz. {pub.group(1)}" if pub and pub_date else None
    ann = re.search(r"OBWIESZCZENIE.*?z\s+dnia\s+" + _DATE, flat)
    announcement = parse_polish_date(*ann.groups()) if ann else None
    st = re.search(r"stanu\s+prawnego\s+na\s+dzień\s+" + _DATE, flat)
    state = parse_polish_date(*st.groups()) if st else None
    base = re.search(r"jednolity\s+tekst\s+ustawy.*?\(\s*Dz\.\s*U\.\s*z\s+(\d{4})\s*r\.\s*poz\.\s*(\d+)\)", flat)
    base_text = f"Dz.U. {base.group(1)} poz. {base.group(2)}" if base else None

    p1_end = flat.find("2. Podany")
    point1 = flat[st.end() if st else 0 : p1_end if p1_end > 0 else len(flat)]
    included = _amendment_refs(point1)

    excluded: list[str] = []
    later: list[tuple[str, date]] = []
    if p1_end > 0:
        point2 = text[text.find("2. Podany") :]
        end = point2.find("Marszałek Sejmu")
        point2 = point2[:end] if end > 0 else point2
        for item in _split_items(point2):
            item_flat = re.sub(r"\s+", " ", item).strip()
            refs = _amendment_refs(item_flat)
            src = refs[0].eli_id if refs else None
            quotes = re.findall(r"„(.*?)”", item_flat)
            prefix = item_flat.split("„")[0].strip().rstrip(":").strip()
            if not quotes:
                excluded.append(item_flat)
            for q in quotes:
                excluded.append(f"{prefix}: „{q}”")
                if src:
                    for m in re.finditer(r"z\s+dniem\s+" + _DATE, q):
                        d = parse_polish_date(*m.groups())
                        if d:
                            later.append((src, d))
    return ObwieszczenieHeader(
        publication=publication, publication_date=pub_date, announcement_date=announcement,
        state_date=state, base_text=base_text, included_amendments=included,
        excluded_provisions=excluded, later_entry_dates=later, text=text.strip(),
    )


def _amendment_refs(s: str) -> list[IncludedAmendment]:
    out: list[IncludedAmendment] = []
    pat = re.compile(
        r"(ustaw\w*\s+z\s+dnia\s+\d{1,2}\s+\w+\s+(\d{4})\s*r\.(?:(?!ustaw\w*\s+z\s+dnia).)*?)"
        r"\(\s*Dz\.\s*U\.\s*(?:z\s+(\d{4})\s*r\.\s*)?poz\.\s*(\d+)\s*\)",
        re.S,
    )
    for m in pat.finditer(s):
        year = m.group(3) or m.group(2)
        eli = f"DU/{year}/{m.group(4)}"
        if all(a.eli_id != eli for a in out):
            out.append(IncludedAmendment(eli, re.sub(r"\s+", " ", m.group(0)).strip()))
    return out


def _split_items(point2: str) -> list[str]:
    """Split point 2 into its numbered items ("1) art. 13 ...") ignoring numbering inside quotes."""
    body = re.sub(r"^2\.\s*Podany.*?nie\s+obejmuje\s*:?", "", point2, count=1, flags=re.S)
    items: list[str] = []
    depth = 0
    cur_start = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "„":
            depth += 1
        elif ch == "”":
            depth = max(0, depth - 1)
        elif depth == 0 and (i == 0 or body[i - 1] == "\n"):
            m = re.match(r"\s*\d+\)\s*art\.", body[i:])
            if m and body[cur_start:i].strip():
                items.append(body[cur_start:i])
                cur_start = i
        i += 1
    if body[cur_start:].strip():
        items.append(body[cur_start:])
    return items


# --------------------------------------------------------------------------- main


def _art_locator(m: re.Match) -> tuple[str | None, tuple[int, str, int], list[str]]:
    warnings: list[str] = []
    sup = m.group("sup") or ""
    if sup.startswith("["):
        warnings.append("indeks górny odczytany z nawiasów w warstwie tekstowej PDF, nie z pozycji znaku")
        sup_n = sup.strip("[]")
    else:
        sup_n = sup.translate(UNSUP)
    raw = f"art. {m.group('num')}{m.group('letter')}" + (f"^{sup_n}" if sup_n else "")
    loc = canonical_locator(raw)
    key = (int(m.group("num")), m.group("letter"), int(sup_n) if sup_n else 0)
    return loc, key, warnings


def parse_published_act_pdf(content: bytes) -> ConsolidatedText:
    """Parse the act's own published text (no obwieszczenie header); used when no TJ exists."""
    return parse_consolidated_pdf(content, has_header=False)


def parse_consolidated_pdf(content: bytes, *, has_header: bool = True) -> ConsolidatedText:
    reader = PdfReader(io.BytesIO(content))
    body_size = _body_font_size(reader)
    header_parts: list[str] = []
    att_lines: list[_Line] = []
    footnotes: dict[str, str] = {}
    warnings: list[str] = []
    in_attachment = not has_header

    for pno, page in enumerate(reader.pages, start=1):
        col = _PageCollector(body_size)
        page.extract_text(visitor_text=col.visit)
        col.finish()
        body = "".join(col.body)
        for w in col.warnings:
            warnings.append(f"s. {pno}: {w}")
        for k, v in _parse_footnotes("".join(col.aux)).items():
            if in_attachment or col.annex_marker:
                footnotes.setdefault(k, v)
        if not in_attachment and col.annex_marker:
            cut = col.annex_marker_pos or 0
            header_parts.append(body[:cut])
            refs = [(p - cut, n) for (p, n) in col.footnote_refs if p >= cut]
            att_lines += _split_lines(body[cut:], pno, refs)
            in_attachment = True
        elif in_attachment:
            att_lines += _split_lines(body, pno, col.footnote_refs)
        else:
            header_parts.append(body)

    if has_header and not in_attachment:
        warnings.append("nie znaleziono początku załącznika (tekstu jednolitego)")
    header = _parse_header("\n".join(header_parts))
    vocab = _build_vocab(att_lines)

    articles: list[ParsedArticle] = []
    annexes: list[ParsedArticle] = []
    cur: ParsedArticle | None = None
    cur_lines: list[str] = []
    mode = "preamble"  # preamble | article | structure | annex
    seen: set[str] = set()
    prev_key: tuple[int, str, int] | None = None

    def close() -> None:
        nonlocal cur, cur_lines
        if cur is not None:
            cur.text = _join_lines(cur_lines, vocab)
            (annexes if cur.locator.startswith("załącznik") else articles).append(cur)
        cur, cur_lines = None, []

    def in_open_quote() -> bool:
        # amending acts quote whole new articles („Art. 125². …”); such headings belong to the current article
        joined = "".join(cur_lines)
        return joined.count("„") > joined.count("”")

    for ln in att_lines:
        t = ln.text
        m = _ART_HEAD.match(t)
        if m and mode == "article" and cur is not None and in_open_quote():
            m = None
        if m and mode != "annex":
            close()
            loc, key, w = _art_locator(m)
            if loc is None:
                warnings.append(f"s. {ln.page}: nieparsowalny nagłówek {t[:30]!r}")
                mode = "structure"
                continue
            if prev_key is not None and key <= prev_key:
                w.append(
                    f"numeracja nie rośnie (poprzedni {prev_key}); możliwy błąd odczytu indeksu górnego"
                )
            prev_key = key
            if loc in seen:
                warnings.append(f"s. {ln.page}: powtórzony lokalizator {loc}; pominięto drugie wystąpienie")
                mode = "structure"
                continue
            seen.add(loc)
            cur = ParsedArticle(locator=loc, heading=m.group(0), text="", pages=[ln.page],
                                footnote_refs=list(ln.footnote_refs), warnings=w)
            cur_lines = [t]
            mode = "article"
            continue
        am = _ANNEX_HEAD.match(t)
        if am and mode in ("article", "structure", "annex"):
            close()
            cur = ParsedArticle(locator=f"załącznik nr {am.group(1)}", heading=t, text="", pages=[ln.page])
            cur_lines = [t]
            mode = "annex"
            continue
        if mode != "annex" and (_STRUCT_HEAD.match(t) or _ANNEXES_BLOCK.match(t)):
            close()
            mode = "structure"
            continue
        if mode in ("article", "annex") and cur is not None:
            cur_lines.append(t)
            if ln.page not in cur.pages:
                cur.pages.append(ln.page)
            cur.footnote_refs += ln.footnote_refs
        # preamble (act title) and structure headings are skipped
    close()

    for a in articles:
        a.warnings += [f"przypis {n}: {footnotes[n]}" for n in a.footnote_refs if n in footnotes and _is_legal_note(footnotes[n])]
    return ConsolidatedText(
        header=header, articles=articles, annexes=annexes, footnotes=footnotes,
        warnings=warnings, page_count=len(reader.pages),
    )


def _is_legal_note(note: str) -> bool:
    """Footnotes that change how the article applies (TK rulings, loss of force)."""
    return bool(re.search(r"Trybunał|utracił|Utracił|traci moc|niezgodn", note))
