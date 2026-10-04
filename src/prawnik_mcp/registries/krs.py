"""KRS (Krajowy Rejestr Sądowy): current and full extracts from the open API of the Ministry of Justice.

Endpoints (no key, no published rate limit; verified 2026-10-04; we use 0.5 req/s):
- GET https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr={P|S}&format=json
- GET https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr={P|S}&format=json
The register letter must match: P (przedsiębiorcy) or S (stowarzyszenia, fundacje, …); a number is
looked up in P first, then in S. Answers observed:
- 200 + JSON (`odpis.naglowekA`/`naglowekP`, `odpis.dane.dzial1..dzial6`);
- 404 problem JSON: no entity with this number in this register;
- 400 problem JSON: malformed number (never sent: numbers are validated locally);
- 204 with an empty body on OdpisAktualny: the entity was struck off. Its full extract then carries
  `naglowekP.stanPozycji = 2` and a last entry "WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO"
  (seen on 0000018507, 0000065491, 0000300472).
`stanPozycji` is undocumented: 1 on active entities, 2 on struck-off ones and 3 on an active
foundation (0000030897) were seen. It is passed through, never interpreted.

A full extract versions every value with entry numbers (`nrWpisuWprow`, `nrWpisuWykr`).
`state_at()` collapses it to the register state after a given entry, in the shape of a current
extract; that gives the card of a struck-off entity (state before the deletion entry) and the
register state on a date.

Privacy. MS masks names and PESEL in structured fields ("F*****", "5**********"); masked values are
passed on as given, never unmasked, and the masked PESEL is not returned at all. Free-text fields
(e.g. `rodzajProkury`) are NOT masked by MS and were seen with full names and PESEL numbers. Before
any text leaves this module (`scrub`): a name written right before "PESEL" is masked, the PESEL is
removed, a name matching the masked shape of a listed person (same initials and lengths) is masked
the same way, and every remaining 11-digit number is removed. Raw responses are kept unchanged as
local snapshots (provenance); they are never committed or returned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from prawnik_mcp.connectors.http import FetchResult, NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import Snapshot
from prawnik_mcp.registries.ids import normalize_regon
from prawnik_mcp.store import Store

SOURCE_ID = "krs"
HOST = "api-krs.ms.gov.pl"
BASE = f"https://{HOST}/api/krs"
ACCEPT = "application/json"
PARSER_VERSION = "krs-json-1"
REGISTERS = ("P", "S")
REGISTER_NAMES = {
    "P": "rejestr przedsiębiorców",
    "S": "rejestr stowarzyszeń, innych organizacji społecznych i zawodowych, fundacji oraz SPZOZ",
}
PROCESSING = ("wyciąg pól z odpisu JSON (API KRS Ministerstwa Sprawiedliwości); imiona, nazwiska i PESEL "
              "zamaskowane przez MS; w polach opisowych prawnik-mcp usunął numery PESEL i zamaskował nazwiska "
              "zapisane przy nich lub pasujące do zamaskowanych osób z odpisu")
ATTRIBUTION = "Źródło: Krajowy Rejestr Sądowy – API KRS Ministerstwa Sprawiedliwości (api-krs.ms.gov.pl)"
FRAGMENT_CHARS = 600  # verbatim fragments in status flags; longer ones end with "[…]"
HISTORY_ITEMS = 100  # per history list (most recent kept)
DELETION = "WYKREŚLENIE"
_NR = ("nrWpisuWprow", "nrWpisuWykr")


def extract_url(krs: str, register: str, *, full: bool = False) -> str:
    return f"{BASE}/{'OdpisPelny' if full else 'OdpisAktualny'}/{krs}?rejestr={register}&format=json"


# --------------------------------------------------------------------------- fetching


@dataclass
class KrsAnswer:
    status: str  # "ok" | "struck_off" (204 on OdpisAktualny) | "not_found" (404 in every register tried)
    register: str | None
    url: str
    requests: int
    odpis: dict | None = None
    fetch: FetchResult | None = None
    snapshot: Snapshot | None = None

    @property
    def head(self) -> dict:
        o = self.odpis or {}
        return o.get("naglowekA") or o.get("naglowekP") or {}


def _load(r: FetchResult) -> dict:
    try:
        odpis = json.loads(r.content)["odpis"]
        if not isinstance(odpis.get("dane"), dict):
            raise KeyError("dane")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise SourceUnavailable(r.url, f"nieoczekiwany format odpowiedzi API KRS ({type(e).__name__})", r.status) from None
    return odpis


def fetch_extract(store: Store, client: PoliteClient, krs: str, *, full: bool = False,
                  registers: Iterable[str] = REGISTERS) -> KrsAnswer:
    """Fetch a current (or full) extract, trying the registers in order. The raw answer is stored as a snapshot.

    Raises SourceUnavailable (with attribute `requests`) on upstream failure."""
    first_url, n = None, 0
    for reg in registers:
        url = extract_url(krs, reg, full=full)
        first_url = first_url or url
        n += 1
        try:
            r = client.get(url, accept=ACCEPT)
        except NotFoundUpstream:
            continue
        except SourceUnavailable as e:
            if e.http_status == 204 and not full:
                return KrsAnswer("struck_off", reg, url, n)
            e.requests = n  # type: ignore[attr-defined]
            raise
        try:
            odpis = _load(r)
            got = str((odpis.get("naglowekA") or odpis.get("naglowekP") or {}).get("numerKRS", krs))
            if got != krs:
                raise SourceUnavailable(url, f"API KRS zwróciło odpis innego numeru ({got})", r.status)
        except SourceUnavailable as e:
            e.requests = n  # type: ignore[attr-defined]
            raise
        snap = store.put_snapshot(SOURCE_ID, r.url, r.content, r.content_type, parser_version=PARSER_VERSION,
                                  fetched_at=r.fetched_at)
        return KrsAnswer("ok", reg, r.url, n, odpis, r, snap)
    return KrsAnswer("not_found", None, first_url or extract_url(krs, "P", full=full), n)


# --------------------------------------------------------------------------- generic helpers


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _as_list(v: Any) -> list:
    return [] if v is None else (v if isinstance(v, list) else [v])


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def iso_date(s: Any) -> str | None:
    """'17.09.2026' -> '2026-09-17'; anything else -> None (never guessed)."""
    m = re.fullmatch(r"\s*(\d{2})\.(\d{2})\.(\d{4})\s*", str(s or ""))
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return None


def _valid_at(item: dict, k: int | None) -> bool:
    wprow, wykr = _int(item.get("nrWpisuWprow")), _int(item.get("nrWpisuWykr"))
    if k is None:
        return item.get("nrWpisuWykr") in (None, "")
    return (wprow is None or wprow <= k) and (wykr is None or wykr > k)


def state_at(node: Any, k: int | None, parent_key: str | None = None) -> Any:
    """Register state after entry `k` (None = current: values not struck out) of a full-extract subtree.

    Versioned lists collapse to their valid value(s); `{"nazwa": X, nr…}` under key "nazwa" unwraps to X.
    A single valid value comes back unwrapped (use `_as_list` where a list is expected). On a current
    extract (no entry numbers) this only drops empty values."""
    if isinstance(node, list):
        if any(isinstance(x, dict) and "nrWpisuWprow" in x for x in node):
            vals = []
            for x in node:
                if not isinstance(x, dict) or not _valid_at(x, k):
                    continue
                rest = {kk: vv for kk, vv in x.items() if kk not in _NR}
                vals.append(rest[parent_key] if len(rest) == 1 and parent_key in rest else state_at(rest, k))
            vals = [v for v in vals if not _empty(v)]
            return None if not vals else (vals[0] if len(vals) == 1 else vals)
        return [y for y in (state_at(x, k, parent_key) for x in node) if not _empty(y)]
    if isinstance(node, dict):
        out = {}
        for kk, vv in node.items():
            y = state_at(vv, k, kk)
            if not _empty(y):
                out[kk] = y
        return out
    return node


def _latest(lst: Any, key: str) -> Any:
    """Value of the most recently introduced version of a full-extract field (struck out or not)."""
    items = [x for x in _as_list(lst) if isinstance(x, dict)]
    if not items:
        return lst
    if "nrWpisuWprow" not in items[0]:
        return items[-1].get(key, items[-1])
    best = max(items, key=lambda x: _int(x.get("nrWpisuWprow")) or 0)
    rest = {kk: vv for kk, vv in best.items() if kk not in _NR}
    return rest[key] if len(rest) == 1 and key in rest else rest


# --------------------------------------------------------------------------- privacy


_W = r"[^\W\d_]+"
_NAME_TOKEN = rf"{_W}(?:\s*-\s*{_W})?"
_PESEL_REF = re.compile(r"\(?\s*(?:NR\s+)?PESEL\s*(?:NR)?\s*[:.]?\s*\d{11}\s*\)?", re.IGNORECASE)
_TAIL = re.compile(rf"((?:{_NAME_TOKEN}\s+){{0,2}}{_NAME_TOKEN})\s*[(,]?\s*$")
_ELEVEN = re.compile(r"(?<!\d)\d{11}(?!\d)")
_MASK_SHAPE = re.compile(r"([^\W\d_])\*+")
_STOP = {
    "TJ", "ALBO", "LUB", "ORAZ", "I", "Z", "ZE", "W", "DO", "NA", "PRZEZ", "JAKO", "PAN", "PANI", "PANEM", "PANIĄ",
    "PROKURENT", "PROKURENTA", "PROKURENTEM", "PROKURENTÓW", "PROKURENTAMI", "PROKURENCI", "PROKURENTKA",
    "PROKURENTKĄ", "CZŁONEK", "CZŁONKA", "CZŁONKIEM", "ZARZĄDU", "PREZES", "PREZESA", "PREZESEM", "WICEPREZES",
    "WICEPREZESEM", "LIKWIDATOR", "LIKWIDATOREM", "WSPÓLNIK", "WSPÓLNIKIEM", "DZIAŁAJĄCY", "DZIAŁAJĄCYM",
    "ŁĄCZNIE", "SAMODZIELNIE", "OSOBA", "OSOBĄ", "INNYM", "JEDNYM", "DRUGIM", "WRAZ", "RAZEM", "PRZY", "OD",
    "DLA", "PEŁNOMOCNIK", "PEŁNOMOCNIKIEM", "KURATOR", "KURATOREM",
}
PESEL_REMOVED = "(PESEL usunięty)"
ELEVEN_REMOVED = "[usunięto: 11 cyfr]"


def mask_words(text: str) -> str:
    """'JAN KOWALSKI' -> 'J** K*******' (the masking style used by MS)."""
    return re.sub(_W, lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def _shape(mask: Any) -> tuple[str, int] | None:
    m = _MASK_SHAPE.fullmatch(mask) if isinstance(mask, str) else None
    return (m.group(1), len(mask)) if m else None


def name_patterns(people: Iterable[dict]) -> list[re.Pattern]:
    """Regexes finding, in free text, the unmasked name of a person listed masked in the extract
    (same first letters and lengths of first name and surname). Used only to mask, never to unmask."""
    def w(sh: tuple[str, int]) -> str:
        return rf"{re.escape(sh[0])}[^\W\d_]{{{sh[1] - 1}}}"

    pats, seen = [], set()
    for p in people:
        im, naz = _d(p.get("imiona")), _d(p.get("nazwisko"))
        f1, f2 = _shape(im.get("imie")), _shape(im.get("imieDrugie"))
        s1, s2 = _shape(naz.get("nazwiskoICzlon")), _shape(naz.get("nazwiskoIICzlon"))
        if not f1 or not s1:
            continue
        rx = w(f1) + (rf"(?:\s+{w(f2)})?" if f2 else "") + r"\s+" + w(s1) + (rf"(?:\s*-\s*{w(s2)})?" if s2 else "")
        if rx not in seen:
            seen.add(rx)
            pats.append(re.compile(rf"(?<![^\W\d_]){rx}(?![^\W\d_])", re.IGNORECASE))
    return pats


def scrub(text: Any, patterns: Iterable[re.Pattern] = ()) -> Any:
    """Remove PESEL numbers from free text and mask the names next to them or matching listed persons."""
    if not isinstance(text, str) or not text:
        return text
    out, pos = [], 0
    for m in _PESEL_REF.finditer(text):
        before = text[pos:m.start()]
        t = _TAIL.search(before)
        if t:
            toks = list(re.finditer(_NAME_TOKEN, t.group(1)))
            while toks and (toks[0].group(0).upper() in _STOP or len(toks[0].group(0)) == 1):
                toks.pop(0)
            if toks:
                a, b = t.start(1) + toks[0].start(), t.start(1) + toks[-1].end()
                before = before[:a] + mask_words(before[a:b]) + before[b:]
        out += [before.rstrip() + " " if before.strip() else before, PESEL_REMOVED]
        pos = m.end()
    out.append(text[pos:])
    s = "".join(out)
    for rx in patterns:
        s = rx.sub(lambda mm: mask_words(mm.group(0)), s)
    return _ELEVEN.sub(ELEVEN_REMOVED, s)


def pesel_valid(s: str) -> bool:
    """11 digits with a PESEL checksum and a plausible birth date (month 1–12 plus century offset 20/40/60/80)."""
    if not re.fullmatch(r"\d{11}", s or "") or not 1 <= int(s[2:4]) % 20 <= 12 or not 1 <= int(s[4:6]) <= 31:
        return False
    w = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    return (10 - sum(int(d) * x for d, x in zip(s[:10], w, strict=True)) % 10) % 10 == int(s[10])


def drop_pesel_like(obj: Any, exempt: frozenset[str] = frozenset({"amount", "amount_decimal"})) -> Any:
    """Defensive last pass over an outgoing structure: no PESEL-valid 11-digit number survives outside `exempt`
    keys. (Free text has already lost every 11-digit number in `scrub`; amounts such as a share capital of
    10 150 715 600,00 PLN are 11 digits too, hence the checksum here.)"""
    if isinstance(obj, str):
        return _ELEVEN.sub(lambda m: ELEVEN_REMOVED if pesel_valid(m.group(0)) else m.group(0), obj)
    if isinstance(obj, list):
        return [drop_pesel_like(x, exempt) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in exempt else drop_pesel_like(v, exempt)) for k, v in obj.items()}
    return obj


def _flat_person(p: dict) -> dict:
    """A person of a current or full extract as {'imiona': {...}, 'nazwisko': {...}} (latest version)."""
    return {"imiona": _d(_latest(p.get("imiona"), "imiona")), "nazwisko": _d(_latest(p.get("nazwisko"), "nazwisko"))}


def people_in(node: Any) -> list[dict]:
    """All natural persons listed in an extract subtree (board, supervisory board, prokurenci, liquidators…)."""
    out: list[dict] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if "nazwisko" in n and "imiona" in n:
                out.append(_flat_person(n))
                return
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return out


def person_name(p: dict) -> tuple[str, bool]:
    """Masked display name and whether every part came masked from MS (if not, it is masked here)."""
    fp = _flat_person(p)
    first = [x for x in (fp["imiona"].get("imie"), fp["imiona"].get("imieDrugie")) if isinstance(x, str) and x]
    last = [x for x in (fp["nazwisko"].get("nazwiskoICzlon"), fp["nazwisko"].get("nazwiskoIICzlon"))
            if isinstance(x, str) and x]
    by_ms = all("*" in x for x in first + last)
    m = [x if "*" in x else mask_words(x) for x in first], [x if "*" in x else mask_words(x) for x in last]
    return f"{' '.join(m[0])} {'-'.join(m[1])}".strip(), by_ms


# --------------------------------------------------------------------------- card


FLAG_SECTIONS = (  # flag, dzial6 key, statutory marker in the firm name
    ("in_liquidation", "likwidacja", "W LIKWIDACJI"),
    ("bankruptcy", "postepowanieUpadlosciowe", "W UPADŁOŚCI"),
    ("restructuring", "postepowanieRestrukturyzacyjneNaprawczePrzymusowaRestrukturyzacjaUporzadkowanaLikwidacja",
     "W RESTRUKTURYZACJI"),
    ("dissolution", "rozwiazanieUniewaznienie", None),
    ("receivership", "zarzadKomisaryczny", None),
)
_KNOWN_D6 = {s for _, s, _ in FLAG_SECTIONS} | {"polaczeniePodzialPrzeksztalcenie"}
_ENDING = re.compile(r"(?:^|\.)(?:zakonczenie|opisZakonczenia)", re.IGNORECASE)


class _Ctx:
    def __init__(self, patterns: list[re.Pattern]):
        self.patterns = patterns
        self.self_masked = False  # a structured name came unmasked from MS and was masked here

    def text(self, s: Any, limit: int | None = None) -> Any:
        s = scrub(s, self.patterns)
        if limit and isinstance(s, str) and len(s) > limit:
            s = s[:limit].rstrip() + " […]"
        return s

    def name(self, p: dict) -> str:
        if p.get("nazwa") and "nazwisko" not in p:
            return self.text(p["nazwa"])
        n, by_ms = person_name(p)
        self.self_masked |= not by_ms
        return n


def _member(p: dict, ctx: _Ctx) -> dict:
    out: dict[str, Any] = {"name_masked": ctx.name(p)}
    for src, dst in (("funkcjaWOrganie", "function"), ("czyZawieszona", "suspended"), ("funkcja", "function")):
        if p.get(src) not in (None, ""):
            out[dst] = p[src]
    return out


def _flatten(node: Any, ctx: _Ctx, path: str = "", out: dict | None = None) -> dict:
    """Non-empty leaves of a dzial6 entry as {'a.b': verbatim}; persons become masked names, PESEL is dropped."""
    out = {} if out is None else out
    if isinstance(node, dict):
        if "nazwisko" in node and "imiona" in node:
            out[path or "osoba"] = ctx.name(node)
            return out
        for k, v in node.items():
            if k == "pesel":
                continue
            _flatten(v, ctx, f"{path}.{k}" if path else k, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _flatten(v, ctx, f"{path}[{i}]" if len(node) > 1 else path, out)
    elif not _empty(node):
        out[path] = ctx.text(node, FRAGMENT_CHARS) if isinstance(node, str) else node
    return out


def _flags(name: str | None, d6: dict, ctx: _Ctx) -> dict:
    flags: dict[str, Any] = {}
    upper = (name or "").upper()
    for flag, key, marker in FLAG_SECTIONS:
        entries = [e for e in (_flatten(x, ctx) for x in _as_list(d6.get(key))) if e]
        flags[flag] = {
            "entry_in_register": bool(entries),
            "ending_entry_present": any(_ENDING.search(p) for e in entries for p in e) if entries else None,
            "name_marker": marker if marker and marker in upper else None,
            "entries": entries,
            "source": f"dzial6.{key}",
        }
    return flags


def _capital(d1: dict) -> dict | None:
    k = _d(_d(d1.get("kapital")).get("wysokoscKapitaluZakladowego"))
    amount = k.get("wartosc")
    if not amount:
        return None
    dec = re.sub(r"[\s.]", "", str(amount)).replace(",", ".")
    return {"amount": amount, "currency": k.get("waluta"),
            "amount_decimal": dec if re.fullmatch(r"\d+(?:\.\d+)?", dec) else None}


def _pkd(d3: dict) -> dict | None:
    items = _as_list(_d(d3.get("przedmiotDzialalnosci")).get("przedmiotPrzewazajacejDzialalnosci"))
    rows = []
    for it in items:
        it = _d(it)
        for p in _as_list(it.get("pozycja", it)):
            p = _d(p)
            if p.get("kodDzial"):
                code = f"{p['kodDzial']}.{p.get('kodKlasa', '')}".rstrip(".")
                rows.append({"code": code + (f".{p['kodPodklasa']}" if p.get("kodPodklasa") else ""),
                             "description": p.get("opis")})
    if not rows:
        return None
    return {**rows[0], **({"other_main_codes": [r["code"] for r in rows[1:]]} if len(rows) > 1 else {})}


def _filings(d3: dict) -> dict | None:
    w = _d(d3.get("wzmiankiOZlozonychDokumentach"))
    rows = []
    for it in _as_list(w.get("wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego")):
        it = _d(it)
        period = it.get("zaOkresOdDo")
        years = re.findall(r"(?:19|20)\d{2}", str(period or ""))
        rows.append({"period_verbatim": period, "filed": iso_date(it.get("dataZlozenia")),
                     "year": int(years[-1]) if years else None})
    if not rows:
        return None
    rows.sort(key=lambda r: (r["year"] or 0, r["filed"] or ""))
    return {"annual_statements_count": len(rows), "years": sorted({r["year"] for r in rows if r["year"]}),
            "latest": rows[-3:],
            "note": "Wzmianki o złożeniu rocznych sprawozdań finansowych (dział 3; rok = rok końca okresu). Treść "
                    "sprawozdań jest w Repozytorium Dokumentów Finansowych, nie w odpisie."}


def _transformations(d6: dict, ctx: _Ctx) -> list[dict] | None:
    out = []
    for x in _as_list(d6.get("polaczeniePodzialPrzeksztalcenie")):
        x = _d(x)
        ents = []
        for k, v in x.items():
            if not k.startswith("podmioty"):
                continue
            for e in _as_list(v):
                e = _d(e)
                krs = _d(e.get("krs")).get("krs") or e.get("numerWRejestrzeAlboEwidencji")
                ents.append({"role": k, "name": ctx.text(e.get("nazwa")), "krs": krs})
        if x.get("okreslenieOkolicznosci") or ents:
            out.append({"circumstance": x.get("okreslenieOkolicznosci"), "entities": ents})
    return out or None


def _other_entries(dane: dict) -> list[str]:
    out = [f"dzial1.{k}" for k, v in _d(dane.get("dzial1")).items() if "zawiesz" in k.lower() and not _empty(v)]
    for sec in ("dzial4", "dzial5"):
        out += [f"{sec}.{k}" for k, v in _d(dane.get(sec)).items() if not _empty(v)]
    out += [f"dzial6.{k}" for k, v in _d(dane.get("dzial6")).items() if k not in _KNOWN_D6 and not _empty(v)]
    return out


def build_card(dane: dict, head: dict, register: str | None, patterns: list[re.Pattern]) -> tuple[dict, bool]:
    """Entity card from a current-extract-shaped `dane` (a current extract, or `state_at` of a full one).

    Returns (card, self_masked): self_masked is True if a structured name came unmasked and was masked here."""
    ctx = _Ctx(patterns)
    d1, d2, d3, d6 = (_d(dane.get(f"dzial{i}")) for i in (1, 2, 3, 6))
    dp, sa = _d(d1.get("danePodmiotu")), _d(d1.get("siedzibaIAdres"))
    idf, seat, adr = _d(dp.get("identyfikatory")), _d(sa.get("siedziba")), _d(sa.get("adres"))
    reps = []
    for r in _as_list(d2.get("reprezentacja")):
        r = _d(r)
        reps.append({"organ": r.get("nazwaOrganu"), "method_verbatim": ctx.text(r.get("sposobReprezentacji")),
                     "members": [_member(_d(p), ctx) for p in _as_list(r.get("sklad"))]})
    address = {k: adr.get(k) for k in ("ulica", "nrDomu", "nrLokalu", "kodPocztowy", "miejscowosc", "poczta", "kraj")
               if adr.get(k)}
    line = " ".join(x for x in (adr.get("ulica"), adr.get("nrDomu")) if x)
    if adr.get("nrLokalu"):
        line += f"/{adr['nrLokalu']}"
    line = ", ".join(x for x in (line, " ".join(y for y in (adr.get("kodPocztowy"), adr.get("miejscowosc")) if y)) if x)
    regon = idf.get("regon")
    card = {
        "name": dp.get("nazwa"),
        "legal_form": dp.get("formaPrawna"),
        "krs": head.get("numerKRS"),
        "register": register,
        "register_name": REGISTER_NAMES.get(register or ""),
        "nip": idf.get("nip"),
        "regon": (normalize_regon(regon) or regon) if regon else None,
        "seat": {k: seat.get(k) for k in ("miejscowosc", "gmina", "powiat", "wojewodztwo", "kraj") if seat.get(k)} or None,
        "address": address or None,
        "address_line": line or None,
        "registration_date": iso_date(head.get("dataRejestracjiWKRS")),
        "last_entry": {"no": head.get("numerOstatniegoWpisu"), "date": iso_date(head.get("dataOstatniegoWpisu"))}
        if head.get("numerOstatniegoWpisu") else None,
        "share_capital": _capital(d1),
        "main_pkd": _pkd(d3),
        "public_benefit_status": dp.get("czyPosiadaStatusOPP"),
        "representation": reps[0] if reps else None,
        "representation_other_organs": reps[1:] or None,
        "supervisory_bodies": [{"organ": _d(o).get("nazwa"), "members": [ctx.name(_d(p)) for p in _as_list(_d(o).get("sklad"))]}
                               for o in _as_list(d2.get("organNadzoru"))] or None,
        "prokura": [{"name_masked": ctx.name(_d(p)), "type_verbatim": ctx.text(_d(p).get("rodzajProkury"))}
                    for p in _as_list(d2.get("prokurenci"))] or None,
        "status_flags": _flags(dp.get("nazwa"), d6, ctx),
        "financial_statements": _filings(d3),
        "transformations": _transformations(d6, ctx),
        "other_register_entries": _other_entries(dane) or None,
    }
    return drop_pesel_like(card), ctx.self_masked


def current_card(odpis: dict, register: str | None) -> tuple[dict, bool]:
    dane = odpis.get("dane") or {}
    return build_card(state_at(dane, None), odpis.get("naglowekA") or {}, register, name_patterns(people_in(dane)))


# --------------------------------------------------------------------------- full extract


def entries_of(odpis_full: dict) -> list[dict]:
    head = odpis_full.get("naglowekP") or {}
    return [{"no": _int(w.get("numerWpisu")), "date": iso_date(w.get("dataWpisu")), "description": w.get("opis"),
             "case_ref": w.get("sygnaturaAktSprawyDotyczacejWpisu"), "final_date": iso_date(w.get("dataUprawomocnienia"))}
            for w in _as_list(head.get("wpis")) if isinstance(w, dict)]


def deletion_entry(odpis_full: dict) -> dict | None:
    dels = [e for e in entries_of(odpis_full) if DELETION in str(e.get("description") or "").upper()]
    return dels[-1] if dels else None


def card_at_deletion(odpis_full: dict, register: str | None) -> tuple[dict, bool]:
    """Card of a struck-off entity: the register state right before the deletion entry."""
    entries = entries_of(odpis_full)
    dele = deletion_entry(odpis_full)
    k = (dele["no"] - 1) if dele and dele["no"] else None
    dane = odpis_full.get("dane") or {}
    head = {"numerKRS": (odpis_full.get("naglowekP") or {}).get("numerKRS"),
            "dataRejestracjiWKRS": next((_dd(e["date"]) for e in entries if e["no"] == 1), None),
            "numerOstatniegoWpisu": entries[-1]["no"] if entries else None,
            "dataOstatniegoWpisu": _dd(entries[-1]["date"]) if entries else None}
    return build_card(state_at(dane, k), head, register, name_patterns(people_in(dane)))


def _dd(iso: str | None) -> str | None:
    """ISO date back to the extract format (for build_card's header)."""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}" if iso else None


def _versions(lst: Any, key: str | None, fmt: Callable[[Any], Any] | None = None, limit: int = HISTORY_ITEMS) -> list[dict]:
    out = []
    for x in _as_list(lst):
        if not isinstance(x, dict) or "nrWpisuWprow" not in x:
            continue
        rest = {kk: vv for kk, vv in x.items() if kk not in _NR}
        val = rest.get(key) if key and key in rest else rest
        out.append({"value": fmt(val) if fmt else val, "from_entry": _int(x.get("nrWpisuWprow")),
                    "to_entry": _int(x.get("nrWpisuWykr"))})
    return out[-limit:]


def _person_interval(p: dict) -> tuple[int | None, int | None]:
    items = [x for x in _as_list(p.get("nazwisko")) if isinstance(x, dict)]
    froms = [_int(x.get("nrWpisuWprow")) for x in items if _int(x.get("nrWpisuWprow")) is not None]
    tos = [_int(x.get("nrWpisuWykr")) for x in items]
    return (min(froms) if froms else None), (None if (not tos or None in tos) else max(t for t in tos if t is not None))


def build_history(odpis_full: dict, on: date | None = None) -> dict:
    """Compact history from a full extract: entries, names, legal forms, seats, capital, representation
    methods, board members and prokurenci with entry intervals (masked names), and optionally the
    register state after the last entry made on or before `on`."""
    dane = odpis_full.get("dane") or {}
    ctx = _Ctx(name_patterns(people_in(dane)))
    d1, d2 = _d(dane.get("dzial1")), _d(dane.get("dzial2"))
    dp, sa = _d(d1.get("danePodmiotu")), _d(d1.get("siedzibaIAdres"))
    entries = entries_of(odpis_full)
    board, methods = [], []
    for organ in _as_list(d2.get("reprezentacja")):
        organ = _d(organ)
        oname = _latest(organ.get("nazwaOrganu"), "nazwaOrganu")
        methods += [{**v, "value": ctx.text(v["value"]), "organ": oname}
                    for v in _versions(organ.get("sposobReprezentacji"), "sposobReprezentacji")]
        for p in _as_list(organ.get("sklad")):
            p = _d(p)
            a, b = _person_interval(p)
            board.append({"name_masked": ctx.name(p), "function": _latest(p.get("funkcjaWOrganie"), "funkcjaWOrganie"),
                          "organ": oname, "from_entry": a, "to_entry": b})
    prokura = []
    for p in _as_list(d2.get("prokurenci")):
        p = _d(p)
        a, b = _person_interval(p)
        prokura.append({"name_masked": ctx.name(p), "type_verbatim": ctx.text(_latest(p.get("rodzajProkury"), "rodzajProkury")),
                        "from_entry": a, "to_entry": b})
    hist: dict[str, Any] = {
        "entries_total": len(entries),
        "entries": entries[-HISTORY_ITEMS:],
        "names": _versions(dp.get("nazwa"), "nazwa"),
        "legal_forms": _versions(dp.get("formaPrawna"), "formaPrawna"),
        "seats": _versions(sa.get("siedziba"), None, lambda v: _d(v).get("miejscowosc")),
        "share_capital": _versions(_d(d1.get("kapital")).get("wysokoscKapitaluZakladowego"), None,
                                   lambda v: {"amount": _d(v).get("wartosc"), "currency": _d(v).get("waluta")}),
        "representation_methods": methods[-HISTORY_ITEMS:],
        "board": board[-HISTORY_ITEMS:],
        "prokura": prokura[-HISTORY_ITEMS:],
        "truncated": len(entries) > HISTORY_ITEMS or len(board) > HISTORY_ITEMS,
        "note": "from_entry/to_entry to numery wpisów (daty w 'entries'). Data wpisu to data dokonania wpisu przez "
                "sąd rejestrowy, nie data zdarzenia: np. powołanie członka zarządu jest skuteczne przed wpisem "
                "(wpis deklaratoryjny).",
    }
    if on is not None:
        done = [e["no"] for e in entries if e["no"] is not None and e["date"] and e["date"] <= on.isoformat()]
        k = max(done) if done else None
        dele = deletion_entry(odpis_full)
        state, note = None, ("Stan rejestru po ostatnim wpisie dokonanym do tej daty; nie musi odpowiadać faktycznemu "
                             "składowi organów w tym dniu (wpisy o członkach organów są deklaratoryjne).")
        if k is None:
            note = "Brak wpisów do tej daty (przed rejestracją w KRS)."
        elif dele and dele["no"] is not None and k >= dele["no"]:
            note = f"Podmiot wykreślony z KRS przed tą datą (wpis nr {dele['no']} z {dele['date']})."
        else:
            card, _ = build_card(state_at(dane, k), {"numerKRS": (odpis_full.get("naglowekP") or {}).get("numerKRS")},
                                 None, ctx.patterns)
            state = {key: card.get(key) for key in ("name", "legal_form", "seat", "representation",
                                                     "representation_other_organs", "prokura", "share_capital")}
        hist["state_on_date"] = {"date": on.isoformat(), "after_entry": k, "state": state, "note": note}
    return drop_pesel_like(hist)
