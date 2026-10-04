"""Days off under the Act of 18 January 1951 on public holidays (eli:DU/1951/28, art. 1).

Changes that matter for dates from 1990: 6 January since 2011-01-01 (Dz.U. 2010 poz. 1459) and 24 December
since 2025-02-01 (Dz.U. 2024 poz. 1965). Sundays are days off (art. 1 pkt 2). Dates before 1990-05-01 are
not supported.
"""

from __future__ import annotations

from datetime import date, timedelta

HOLIDAYS_ACT = "eli:DU/1951/28"
SUPPORTED_FROM = date(1990, 5, 1)
EPIPHANY_FROM = date(2011, 1, 1)
CHRISTMAS_EVE_FROM = date(2025, 2, 1)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def holidays(year: int) -> dict[date, str]:
    """Statutory days off in a year (Sundays not listed)."""
    easter = easter_sunday(year)
    out = {
        date(year, 1, 1): "Nowy Rok",
        easter: "pierwszy dzień Wielkiej Nocy",
        easter + timedelta(days=1): "drugi dzień Wielkiej Nocy",
        date(year, 5, 1): "Święto Państwowe (1 maja)",
        date(year, 5, 3): "Święto Narodowe Trzeciego Maja",
        easter + timedelta(days=49): "pierwszy dzień Zielonych Świątek",
        easter + timedelta(days=60): "Boże Ciało",
        date(year, 8, 15): "Wniebowzięcie Najświętszej Maryi Panny",
        date(year, 11, 1): "Wszystkich Świętych",
        date(year, 11, 11): "Narodowe Święto Niepodległości",
        date(year, 12, 25): "pierwszy dzień Bożego Narodzenia",
        date(year, 12, 26): "drugi dzień Bożego Narodzenia",
    }
    if date(year, 1, 6) >= EPIPHANY_FROM:
        out[date(year, 1, 6)] = "Święto Trzech Króli"
    if date(year, 12, 24) >= CHRISTMAS_EVE_FROM:
        out[date(year, 12, 24)] = "Wigilia Bożego Narodzenia"
    return out


def day_off_reason(d: date) -> str | None:
    """Why `d` is a statutory day off (holiday or Sunday), else None. Saturdays are not days off by statute."""
    name = holidays(d.year).get(d)
    if name and d.weekday() == 6:
        return f"{name} (niedziela)"
    if name:
        return name
    if d.weekday() == 6:
        return "niedziela"
    return None
