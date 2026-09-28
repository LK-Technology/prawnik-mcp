"""Temporal status of a provision version for a given relevant date.

Conservative by design: `valid_from <= date` alone is never treated as proof that a
wording applied on that date. Anything we cannot establish is `unknown`.
"""

from __future__ import annotations

from datetime import date

from prawnik_mcp.contracts import ProvisionVersion, TemporalStatus


def _parse_date(v: object) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def amendment_dates(amendments: list[dict] | None) -> dict[str, date | None]:
    """{"id": ..., "date": "YYYY-MM-DD"} list -> {id: date}. Unparseable dates map to None."""
    out: dict[str, date | None] = {}
    for a in amendments or []:
        if isinstance(a, dict) and a.get("id"):
            out[str(a["id"])] = _parse_date(a.get("date"))
    return out


def _lookup(change: str, dates: dict[str, date | None]) -> tuple[bool, date | None]:
    if change in dates:
        return True, dates[change]
    # tolerate "eli:DU/2025/1172" vs "DU/2025/1172"
    bare = change.removeprefix("eli:")
    for k, v in dates.items():
        if k.removeprefix("eli:") == bare:
            return True, v
    return False, None


def temporal_status_for(
    p: ProvisionVersion,
    relevant_date: date | None,
    *,
    amendments: list[dict] | None = None,
    today: date | None = None,
) -> tuple[TemporalStatus, list[str]]:
    """Return (status, reasons). Reasons are Polish, meant for the user.

    `amendments` = LegalDocument.metadata["amendments"] ([{"id", "date"}]) of the text version,
    used to date `pending_changes`. `today` is injectable for tests.
    """
    if relevant_date is None:
        return TemporalStatus.not_requested, []

    today = today or date.today()
    reasons: list[str] = []
    if relevant_date > today:
        return TemporalStatus.unknown, [
            f"data przyszła ({relevant_date.isoformat()}): nie da się ustalić brzmienia przepisu na datę, "
            "która jeszcze nie nastąpiła – możliwe zmiany prawa"
        ]

    basis = p.temporal_basis

    # ---------------------------------------------------------------- curated interval
    if basis == TemporalStatus.confirmed:
        if p.valid_from is None:
            return TemporalStatus.unknown, ["wersja oznaczona jako zweryfikowana, ale bez daty początkowej"]
        if relevant_date < p.valid_from:
            return TemporalStatus.unknown, [
                f"data zdarzenia ({relevant_date.isoformat()}) jest wcześniejsza niż początek "
                f"zweryfikowanego okresu obowiązywania ({p.valid_from.isoformat()})"
            ]
        if p.valid_to is not None:
            if relevant_date <= p.valid_to:
                return TemporalStatus.confirmed, [
                    f"zweryfikowany okres obowiązywania {p.valid_from.isoformat()} – {p.valid_to.isoformat()}"
                ]
            return TemporalStatus.unknown, [
                f"data zdarzenia po końcu zweryfikowanego okresu ({p.valid_to.isoformat()})"
            ]
        # open-ended curated interval: only confirmed up to the date the text was checked
        if p.text_state_date and relevant_date <= p.text_state_date:
            return TemporalStatus.confirmed, [
                f"zweryfikowany okres od {p.valid_from.isoformat()}, sprawdzony do {p.text_state_date.isoformat()}"
            ]
        return TemporalStatus.unknown, [
            "zweryfikowany okres nie ma daty końcowej ani daty sprawdzenia obejmującej datę zdarzenia"
        ]

    # ---------------------------------------------------------------- consolidated text
    if basis == TemporalStatus.consolidated_text:
        if p.text_state_date is None:
            return TemporalStatus.unknown, ["tekst jednolity bez znanej daty stanu prawnego"]
        state = p.text_state_date.isoformat()
        status = TemporalStatus.consolidated_text
        if relevant_date < p.text_state_date:
            reasons.append(
                f"tekst jednolity odzwierciedla stan na {state}; brzmienie na datę zdarzenia "
                f"({relevant_date.isoformat()}) nieustalone"
            )
            status = TemporalStatus.unknown

        dates = amendment_dates(amendments)
        for change in p.pending_changes:
            known, d = _lookup(change, dates)
            if not known or d is None:
                reasons.append(
                    f"tekst jednolity obejmuje zmianę {change} o nieustalonej dacie wejścia w życie – "
                    "brzmienie może jeszcze nie obowiązywać"
                )
                status = TemporalStatus.unknown
            elif d > relevant_date:
                reasons.append(
                    f"tekst jednolity obejmuje zmianę {change} wchodzącą w życie {d.isoformat()}, "
                    f"po dacie zdarzenia ({relevant_date.isoformat()}) – brzmienie może nie dotyczyć tej daty"
                )
                status = TemporalStatus.unknown

        if p.excluded_provisions:
            reasons.append(
                "istnieją przepisy przejściowe nieobjęte tekstem jednolitym ("
                + "; ".join(p.excluded_provisions)
                + ") – trzeba sprawdzić, czy do tej sprawy stosuje się brzmienie dotychczasowe"
            )
            known_dates = [d for d in dates.values() if d is not None]
            latest = max(known_dates) if known_dates else None
            if latest is None or relevant_date < latest:
                status = TemporalStatus.unknown
        return status, reasons

    # ---------------------------------------------------------------- original publication
    if basis == TemporalStatus.original_publication:
        pub = p.text_state_date or p.valid_from
        if pub is None or relevant_date > pub:
            return TemporalStatus.unknown, [
                "tekst w brzmieniu pierwotnym (bez konsolidacji); nie ustalono, czy nie został zmieniony "
                "przed datą zdarzenia"
            ]
        # event on/before publication: the act may not have existed or been in force yet
        return TemporalStatus.unknown, [
            "tekst w brzmieniu pierwotnym; data zdarzenia nie jest późniejsza niż publikacja – "
            "akt mógł jeszcze nie obowiązywać (nie ustalono daty wejścia w życie)"
        ]

    return TemporalStatus.unknown, ["brak podstawy do ustalenia brzmienia na datę zdarzenia"]
