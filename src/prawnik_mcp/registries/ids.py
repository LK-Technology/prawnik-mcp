"""Identifiers used by the registry lookups: NIP, REGON, KRS, Polish NRB/IBAN and EU VAT numbers.

Validation is local and offline. The VAT white list does not check NIP checksums (a bad checksum is
answered like an unknown NIP, which would also burn the daily quota), so every identifier is validated
here before any request is made.

Searching by a person's name, surname or PESEL is deliberately not supported: an 11-digit number is
refused as a possible PESEL and free text is refused as a name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["nip", "regon", "krs", "nrb", "eu_vat", "invalid"]

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)

# Number part (after the country prefix) per VIES member state code. Greece is EL in VIES (GR is
# accepted on input and rewritten); XI is Northern Ireland.
EU_VAT_FORMATS: dict[str, str] = {
    "AT": r"U\d{8}", "BE": r"[01]\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]", "CZ": r"\d{8,10}",
    "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}", "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}", "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}", "HR": r"\d{11}", "HU": r"\d{8}",
    "IE": r"\d[A-Z0-9+*]\d{5}[A-Z]{1,2}", "IT": r"\d{11}", "LT": r"\d{9}|\d{12}", "LU": r"\d{8}",
    "LV": r"\d{11}", "MT": r"\d{8}", "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}", "RO": r"\d{2,10}",
    "SE": r"\d{12}", "SI": r"\d{8}", "SK": r"\d{10}", "XI": r"\d{9}|\d{12}|GD\d{3}|HA\d{3}",
}

_SEP = re.compile(r"[\s\-./]")
_PREFIX = re.compile(r"^\s*(NIP|KRS|REGON|PESEL|NRB|IBAN|VAT(?:[\s-]?(?:UE|EU))?)(?![^\W\d_])\s*[:#]?\s*(.*)$", re.I)

NAME_SEARCH_REFUSED = ("Wyszukiwanie po nazwie, imieniu lub nazwisku nie jest obsługiwane; "
                       "podaj NIP, REGON, numer KRS albo numer VAT UE (z prefiksem kraju).")
PESEL_REFUSED = "Wyszukiwanie po numerze PESEL nie jest obsługiwane (dane osobowe)."


@dataclass(frozen=True)
class Classified:
    kind: Kind
    value: str | None  # normalised value; for eu_vat the number without the country prefix
    country: str | None = None  # eu_vat only (VIES code, e.g. "EL")
    reason: str | None = None  # why the identifier is invalid (Polish, user-facing)
    note: str | None = None  # normalisation note (e.g. "GR -> EL", zero-padded KRS)

    @property
    def ok(self) -> bool:
        return self.kind != "invalid"

    @property
    def vat_id(self) -> str | None:
        return f"{self.country}{self.value}" if self.kind == "eu_vat" else None


def _mod11(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11


def compact(s: str) -> str:
    """Remove spaces, dashes, dots and slashes; upper-case."""
    return _SEP.sub("", s or "").upper()


def nip_ok(nip: str) -> bool:
    """10 digits, weights 6,5,7,2,3,4,5,6,7, sum mod 11 equals the last digit (10 is never valid)."""
    if not re.fullmatch(r"\d{10}", nip or ""):
        return False
    r = _mod11(nip[:9], NIP_WEIGHTS)
    return r != 10 and r == int(nip[9])


def regon_ok(regon: str) -> bool:
    """9-digit REGON, or 14-digit REGON of a local unit (its first 9 digits must be a valid REGON too)."""
    regon = regon or ""
    if re.fullmatch(r"\d{9}", regon):
        return _mod11(regon[:8], REGON9_WEIGHTS) % 10 == int(regon[8])
    if re.fullmatch(r"\d{14}", regon):
        return regon_ok(regon[:9]) and _mod11(regon[:13], REGON14_WEIGHTS) % 10 == int(regon[13])
    return False


def nrb_ok(nrb: str) -> bool:
    """26-digit Polish account number (NRB = IBAN without 'PL'): mod 97 of the rearranged IBAN equals 1."""
    if not re.fullmatch(r"\d{26}", nrb or ""):
        return False
    return int(nrb[2:] + "2521" + nrb[:2]) % 97 == 1  # 'P' = 25, 'L' = 21


def normalize_nip(s: str) -> str | None:
    t = compact(s)
    if t.startswith("PL"):
        t = t[2:]
    return t if nip_ok(t) else None


def normalize_regon(s: str) -> str | None:
    """Valid 9- or 14-digit REGON. KRS writes a 9-digit REGON padded with '00000' (not a valid 14-digit
    REGON); such values are returned as the 9 digits."""
    t = compact(s)
    if re.fullmatch(r"\d{9}0{5}", t) and regon_ok(t[:9]):
        return t[:9]  # local units are numbered from 0001, so '0000' + check digit 0 is KRS padding
    return t if regon_ok(t) else None


def normalize_krs(s: str) -> str | None:
    """KRS numbers have 10 digits; shorter input is zero-padded ('28860' -> '0000028860')."""
    t = compact(s)
    if not re.fullmatch(r"\d{1,10}", t) or int(t) == 0:
        return None
    return t.zfill(10)


def normalize_nrb(s: str) -> str | None:
    """Accepts '61 1090 …', 'PL61109…' and IBAN-style groups; returns the 26 digits or None."""
    t = compact(s)
    if t.startswith("PL"):
        t = t[2:]
    return t if nrb_ok(t) else None


def mask_account(nrb: str) -> str:
    """Show only the last 4 digits of an account number."""
    return f"…{nrb[-4:]}" if nrb else ""


def normalize_eu_vat(s: str) -> tuple[str, str] | None:
    """'PL 774-000-14-54' -> ('PL', '7740001454'); 'GR…' -> ('EL', …). None if the format is wrong."""
    t = compact(s)
    if len(t) < 4 or not t[:2].isalpha():
        return None
    cc, num = t[:2], t[2:]
    if cc == "GR":
        cc = "EL"
    fmt = EU_VAT_FORMATS.get(cc)
    if not fmt or not re.fullmatch(fmt, num):
        return None
    if cc == "PL" and not nip_ok(num):
        return None
    return cc, num


def _invalid(reason: str) -> Classified:
    return Classified("invalid", None, reason=reason)


def _as(kind: str, raw: str) -> Classified:
    t = compact(raw)
    if kind == "NIP":
        if t.startswith("PL"):
            t = t[2:]
        if not re.fullmatch(r"\d{10}", t):
            return _invalid("NIP musi mieć 10 cyfr.")
        return Classified("nip", t) if nip_ok(t) else _invalid("Nieprawidłowa suma kontrolna NIP.")
    if kind == "KRS":
        v = normalize_krs(t)
        note = "numer KRS uzupełniony zerami do 10 cyfr" if v and v != t else None
        return Classified("krs", v, note=note) if v else _invalid("Numer KRS to do 10 cyfr.")
    if kind == "REGON":
        if not re.fullmatch(r"\d{9}|\d{14}", t):
            return _invalid("REGON musi mieć 9 albo 14 cyfr.")
        v = normalize_regon(t)
        note = "REGON w zapisie KRS (9 cyfr + 00000) skrócony do 9 cyfr" if v and v != t else None
        return Classified("regon", v, note=note) if v else _invalid("Nieprawidłowa suma kontrolna REGON.")
    if kind == "PESEL":
        return _invalid(PESEL_REFUSED)
    if kind in ("NRB", "IBAN"):
        v = normalize_nrb(t)
        return Classified("nrb", v) if v else _invalid("Nieprawidłowy numer rachunku (26 cyfr, kontrola mod 97).")
    # VAT / VAT-UE / VAT EU
    vat = normalize_eu_vat(t)
    if not vat:
        return _invalid("Nieprawidłowy numer VAT UE: oczekiwany prefiks kraju (np. PL, DE, EL) i numer w formacie VIES.")
    return Classified("eu_vat", vat[1], country=vat[0], note="GR zamienione na EL (kod VIES)" if t.startswith("GR") else None)


def classify(identifier: str) -> Classified:
    """Recognise and validate an identifier. Never makes a network request.

    Explicit labels win ('NIP …', 'KRS …', 'REGON …', 'VAT …'). Bare numbers: 10 digits starting with 0 =
    KRS (tax-office prefixes of a NIP never start with 0), other 10 digits = NIP; 9 digits = REGON unless
    it starts with 0 (then KRS); 14 = REGON; 26 = bank account; 1–8 = KRS (zero-padded); 11 digits are
    refused as a possible PESEL. Two letters + number = EU VAT (PL + 26 digits = Polish IBAN)."""
    raw = (identifier or "").strip()
    if not raw:
        return _invalid("Pusty identyfikator.")
    if len(raw) > 64:
        return _invalid("Identyfikator jest za długi.")
    m = _PREFIX.match(raw)
    if m and m.group(2):
        label = re.sub(r"[\s-]", "", m.group(1).upper())
        return _as("VAT" if label.startswith("VAT") else label, m.group(2))
    t = compact(raw)
    if re.fullmatch(r"PL\d{26}", t):
        return _as("NRB", t)
    if (re.fullmatch(r"[A-Z]{2}[0-9A-Z+*]{2,13}", t) and re.search(r"\d", t[2:])
            and (t[:2] in EU_VAT_FORMATS or t[:2] == "GR")):
        return _as("VAT", t)
    if t.isdigit():
        n = len(t)
        if n == 10:
            return _as("KRS", t) if t.startswith("0") else _as("NIP", t)
        if n == 9:
            return _as("KRS", t) if t.startswith("0") else _as("REGON", t)
        if n == 14:
            return _as("REGON", t)
        if n == 26:
            return _as("NRB", t)
        if n == 11:
            return _invalid("11 cyfr: możliwy numer PESEL. " + PESEL_REFUSED)
        if n <= 8:
            return _as("KRS", t)
        return _invalid(f"Nierozpoznany identyfikator ({n} cyfr): podaj NIP (10), REGON (9/14) albo KRS (do 10).")
    if re.search(r"[^\W\d_]", raw):
        return _invalid(NAME_SEARCH_REFUSED)
    return _invalid("Nierozpoznany identyfikator.")
