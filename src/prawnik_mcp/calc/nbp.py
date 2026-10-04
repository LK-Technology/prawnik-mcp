"""NBP average exchange rate from the last business day before a date (api.nbp.pl, no key).

Used by art. 31a ust. 1 of the VAT Act (eli:DU/2004/535) and art. 11a ust. 1–3 of the PIT Act (eli:DU/1991/350):
the average NBP rate "z ostatniego dnia roboczego poprzedzającego" the event. A day with a published table A
(or B) is treated as a business day.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient

API = "https://api.nbp.pl/api/exchangerates/rates"
ACCEPT = "application/json"
LEGAL_BASIS = {
    "vat": [("eli:DU/2004/535", "art. 31a ust. 1"), ("eli:DU/2004/535", "art. 31a ust. 2")],
    "pit": [("eli:DU/1991/350", "art. 11a ust. 1"), ("eli:DU/1991/350", "art. 11a ust. 2"),
            ("eli:DU/1991/350", "art. 11a ust. 3")],
}
_CODE = re.compile(r"^[A-Za-z]{3}$")


@dataclass
class Rate:
    currency: str
    table: str
    rate: float
    table_no: str
    effective_date: date
    url: str
    fetched_at: datetime


def range_url(code: str, table: str, start: date, end: date) -> str:
    return f"{API}/{table.lower()}/{code.lower()}/{start.isoformat()}/{end.isoformat()}/?format=json"


def rate_before(client: PoliteClient, code: str, event: date, *, table: str = "A") -> Rate | None:
    """Last published average rate strictly before `event`. None when the currency is not in the table."""
    if not _CODE.match(code):
        raise ValueError("currency must be a 3-letter ISO code (e.g. EUR)")
    if table.upper() not in ("A", "B"):
        raise ValueError("table must be A or B")
    if code.upper() == "PLN":
        raise ValueError("PLN needs no conversion")
    end = event - timedelta(days=1)
    for window in (14, 45):  # table B is weekly; holidays can leave long gaps
        url = range_url(code, table, end - timedelta(days=window), end)
        try:
            r = client.get(url, accept=ACCEPT)
        except NotFoundUpstream:
            continue  # no table in the window, or the currency is not in this table
        rates = json.loads(r.content).get("rates") or []
        if rates:
            last = max(rates, key=lambda x: x["effectiveDate"])
            return Rate(currency=code.upper(), table=table.upper(), rate=float(last["mid"]), table_no=last["no"],
                        effective_date=date.fromisoformat(last["effectiveDate"]), url=url, fetched_at=r.fetched_at)
    return None
