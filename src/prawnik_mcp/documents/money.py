"""Money helpers: parsing, Polish formatting, Polish amount-in-words, PL bank account checksum.

No interest or deadline arithmetic lives here (out of MVP scope).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_UNITS = ["", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
_TEENS = ["dziesięć", "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście",
          "szesnaście", "siedemnaście", "osiemnaście", "dziewiętnaście"]
_TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt",
         "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]
_HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset",
             "osiemset", "dziewięćset"]
_GROUPS = [None, ("tysiąc", "tysiące", "tysięcy"), ("milion", "miliony", "milionów"),
           ("miliard", "miliardy", "miliardów")]
CURRENCY_FORMS = {
    "PLN": ("złoty", "złote", "złotych"),
    "EUR": ("euro", "euro", "euro"),
    "USD": ("dolar", "dolary", "dolarów"),
}
_GROSZ = ("grosz", "grosze", "groszy")
_CENT = ("cent", "centy", "centów")


def plural_form(n: int, forms: tuple[str, str, str]) -> str:
    if n == 1:
        return forms[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return forms[1]
    return forms[2]


def _triple(n: int) -> list[str]:
    h, rest = divmod(n, 100)
    t, u = divmod(rest, 10)
    out = [_HUNDREDS[h]] if h else []
    if t == 1:
        out.append(_TEENS[u])
    else:
        if t:
            out.append(_TENS[t])
        if u:
            out.append(_UNITS[u])
    return out


def int_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    if n < 0 or n >= 10**12:
        raise ValueError("unsupported number")
    words: list[str] = []
    groups = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g:
            continue
        if i == 0:
            words += _triple(g)
        elif g == 1:
            words.append(_GROUPS[i][0])
        else:
            words += _triple(g) + [plural_form(g, _GROUPS[i])]
    return " ".join(words)


def parse_amount(value) -> Decimal | None:
    """'1234.50' / '1 234,50' / 1234.5 -> Decimal; None if not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if not isinstance(value, str):
        return None
    s = value.strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"\d+,\d+", s):
        s = s.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def decimal_places(d: Decimal) -> int:
    exp = d.normalize().as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 99


def format_amount(d: Decimal, currency: str) -> str:
    """Polish formatting: 1 234,50 PLN."""
    q = d.quantize(Decimal("0.01"))
    whole, frac = f"{q:.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{' '.join(groups)},{frac} {currency}"


def amount_in_words(d: Decimal, currency: str) -> str:
    """'1234.50', PLN -> 'tysiąc dwieście trzydzieści cztery złote 50/100'."""
    q = d.quantize(Decimal("0.01"))
    whole = int(q)
    cents = int((q - whole) * 100)
    forms = CURRENCY_FORMS.get(currency)
    cur = plural_form(whole, forms) if forms else currency
    return f"{int_to_words(whole)} {cur} {cents:02d}/100"


def _norm_words(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower()
    s = re.sub(r"^\s*słownie\s*:?", "", s)
    s = re.sub(r"[^\w/ ]", " ", s)
    return " ".join(s.split())


def words_match(words: str, d: Decimal, currency: str) -> bool:
    """True when the user's amount-in-words names the same amount.

    Accepted: '<number words> [currency word] [NN/100 | <cents words> grosz* | nothing when 0]'."""
    q = d.quantize(Decimal("0.01"))
    whole = int(q)
    cents = int((q - whole) * 100)
    base = int_to_words(whole)
    cur_opts = [""] + list(set(CURRENCY_FORMS.get(currency, ()))) + [currency.lower()]
    sub = _GROSZ if currency == "PLN" else _CENT
    cent_opts = [f"{cents:02d}/100", f"{cents}/100"]
    cent_opts += [f"{int_to_words(cents)} {f}" for f in set(sub)] if cents else [""]
    if cents == 0:
        cent_opts.append("")
    target = _norm_words(words)
    for c in cur_opts:
        for ct in cent_opts:
            if _norm_words(" ".join(x for x in (base, c, ct) if x)) == target:
                return True
    return False


def normalize_account(s: str) -> str:
    return re.sub(r"[\s-]", "", s).upper()


def valid_pl_account(s: str) -> bool:
    """NRB (26 digits) or IBAN 'PL'+26 digits with a valid mod-97 checksum."""
    a = normalize_account(s)
    if a.startswith("PL"):
        a = a[2:]
    if not re.fullmatch(r"\d{26}", a):
        return False
    rearranged = a[2:] + "2521" + a[:2]  # 'PL' -> 25 21
    return int(rearranged) % 97 == 1


def format_account(s: str) -> str:
    a = normalize_account(s)
    prefix = ""
    if a.startswith("PL"):
        prefix, a = "PL", a[2:]
    return prefix + a[:2] + " " + " ".join(a[i:i + 4] for i in range(2, len(a), 4))
