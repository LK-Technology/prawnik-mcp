"""Statutory deadline arithmetic for tax, civil and administrative law.

Rules (verify the wording with get_legal_document before relying on them):
- tax: Ordynacja podatkowa art. 12 § 1–5 (eli:DU/1997/926);
- civil (also court civil procedure, art. 165 § 1 kpc refers to civil law): Kodeks cywilny art. 111, 112, 115
  (eli:DU/1964/93);
- administrative: Kodeks postępowania administracyjnego art. 57 § 1–4 (eli:DU/1960/168).

All three: the day of the triggering event is not counted for terms in days; terms in weeks/months/years end on
the corresponding day (end of month if there is none; for years on 29 February the tax and administrative
codes take the preceding day, which gives the same date); an end falling on a Saturday or a statutory day off
moves to the next day that is neither. What is NOT modelled: when the term starts (service, deemed service),
keeping the term by posting or electronic delivery, suspensions and interruptions, terms in hours, and special
rules in other statutes ("chyba że ustawy podatkowe stanowią inaczej").
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from prawnik_mcp.calc.holidays import HOLIDAYS_ACT, SUPPORTED_FROM, day_off_reason

UNITS = ("days", "weeks", "months", "years")
REGIMES: dict[str, dict] = {
    "tax": {"label": "Ordynacja podatkowa", "basis": [
        ("eli:DU/1997/926", "art. 12 § 1"), ("eli:DU/1997/926", "art. 12 § 2"), ("eli:DU/1997/926", "art. 12 § 3"),
        ("eli:DU/1997/926", "art. 12 § 4"), ("eli:DU/1997/926", "art. 12 § 5")]},
    "civil": {"label": "Kodeks cywilny", "basis": [
        ("eli:DU/1964/93", "art. 111"), ("eli:DU/1964/93", "art. 112"), ("eli:DU/1964/93", "art. 115")]},
    "administrative": {"label": "Kodeks postępowania administracyjnego", "basis": [
        ("eli:DU/1960/168", "art. 57 § 1"), ("eli:DU/1960/168", "art. 57 § 2"), ("eli:DU/1960/168", "art. 57 § 3"),
        ("eli:DU/1960/168", "art. 57 § 3a"), ("eli:DU/1960/168", "art. 57 § 4")]},
}
COVID_WINDOW = (date(2020, 3, 14), date(2020, 5, 23))


@dataclass
class DeadlineResult:
    end: date
    nominal_end: date
    shifted_over: list[tuple[str, str]] = field(default_factory=list)  # (date, reason)
    basis: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def nominal_end(start: date, amount: int, unit: str) -> date:
    if unit == "days":
        return start + timedelta(days=amount)
    if unit == "weeks":
        return start + timedelta(weeks=amount)
    if unit == "months":
        return _add_months(start, amount)
    if unit == "years":
        try:
            return start.replace(year=start.year + amount)
        except ValueError:  # 29 February -> 28 February
            return date(start.year + amount, 2, 28)
    raise ValueError(f"unit must be one of {UNITS}")


def compute(start: date, amount: int, unit: str, regime: str) -> DeadlineResult:
    if regime not in REGIMES:
        raise ValueError(f"regime must be one of {tuple(REGIMES)}")
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}")
    if amount < 1 or amount > 3650:
        raise ValueError("amount must be between 1 and 3650")
    if start < SUPPORTED_FROM:
        raise ValueError(f"dates before {SUPPORTED_FROM.isoformat()} are not supported")
    end = nominal = nominal_end(start, amount, unit)
    shifted: list[tuple[str, str]] = []
    while True:
        reason = "sobota" if end.weekday() == 5 else day_off_reason(end)
        if not reason:
            break
        shifted.append((end.isoformat(), reason))
        end += timedelta(days=1)
    res = DeadlineResult(end=end, nominal_end=nominal, shifted_over=shifted,
                         basis=list(REGIMES[regime]["basis"]) + [(HOLIDAYS_ACT, "art. 1")])
    if start <= COVID_WINDOW[1] and end >= COVID_WINDOW[0]:
        res.warnings.append("Okres obejmuje marzec–maj 2020: biegi wielu terminów były wtedy wstrzymane lub zawieszone "
                            "(ustawa z 2 marca 2020 r., Dz.U. 2020 poz. 374 ze zm.). Kalkulator tego nie uwzględnia.")
    return res
