"""Wykaz podatników VAT (the "white list") — API of the Ministry of Finance.

Endpoints (no key; verified 2026-10-04):
- GET https://wl-api.mf.gov.pl/api/search/nip/{nip}?date=YYYY-MM-DD  (also /search/regon/{regon})
  -> {"result": {"subject": {...} | null, "requestDateTime", "requestId"}}
- GET https://wl-api.mf.gov.pl/api/check/nip/{nip}/bank-account/{nrb}?date=YYYY-MM-DD  (also /check/regon/…)
  -> {"result": {"accountAssigned": "TAK" | "NIE", "requestDateTime", "requestId"}}
`date` is required; it may not be in the future (WL-103) nor more than 5 years back.

Quotas (MF, https://www.gov.pl/web/kas/api-wykazu-podatnikow-vat, read 2026-10-04): search 100 requests a
day (up to 30 subjects each), check 5000 a day; after either is used up "access to the API may be blocked
until 0:00", and the block also covers the podatki.gov.pl web search. Hence:
- persistent per-day counters (table registry_quota, Europe/Warsaw day) with safety margins (search <= 80,
  check <= 4500); a call is counted before it is sent; at the limit nothing is sent and the status is
  daily_quota_exhausted;
- no automatic retry of any white-list call (not even on 429/5xx): see `no_retry`;
- HTTP 429 is taken as an upstream quota refusal and fills the local counter for the day. The upstream
  answer to an exhausted quota was not observed (deliberately not provoked); 429 is an assumption.
The API does not validate NIP checksums (a bad checksum is answered like an unknown NIP): callers validate
first (ids.nip_ok), which also saves quota.

Privacy: a subject with a PESEL or without a KRS number is treated as a (possible) natural person — sole
traders, civil-law partnerships, but also e.g. municipalities — and reduced to name, NIP, VAT status, town,
number of accounts and registration/removal dates; its raw answer is not stored (a minimised copy with the
request id is). For other subjects representatives, clerks and partners are returned by name only (no
PESEL, no NIP). Bank accounts are never listed, only counted; an account supplied by the caller is checked
with the check endpoint (TAK/NIE + requestId) and shown masked (last 4 digits).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from prawnik_mcp.connectors.http import FetchResult, PoliteClient, UpstreamError
from prawnik_mcp.registries import SourceOutcome, SourceStatus
from prawnik_mcp.registries.ids import mask_account
from prawnik_mcp.store import Store

SOURCE_ID = "wl_vat"
HOST = "wl-api.mf.gov.pl"
BASE = f"https://{HOST}/api"
ACCEPT = "application/json"
PARSER_VERSION = "wl-json-1"
TZ = ZoneInfo("Europe/Warsaw")
LIMITS = {"search": 80, "check": 4500}  # our safety margins
UPSTREAM_LIMITS = {"search": 100, "check": 5000}  # published by MF
QUOTA_KEEP_DAYS = 31
MAX_YEARS_BACK = 5
PROCESSING = ("wyciąg pól z odpowiedzi API wykazu podatników VAT; bez numerów rachunków, PESEL i NIP osób; "
              "dla osób fizycznych (lub podmiotów bez KRS) tylko dane minimalne")
ATTRIBUTION = "Źródło: Wykaz podatników VAT, Ministerstwo Finansów (wl-api.mf.gov.pl)"
REQUEST_ID_NOTE = ("requestId to elektroniczny identyfikator zapytania nadany przez MF: potwierdza, kiedy i o jaki "
                   "podmiot (oraz dzień stanu) zapytano wykaz. Zachowaj go razem z datą jako dowód sprawdzenia, "
                   "np. dla organów podatkowych.")


def warsaw_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(TZ).date()


def date_error(on: date, today: date) -> str | None:
    """Local check of the `date` parameter (the API answers WL-103 for future dates)."""
    if on > today:
        return f"Data {on.isoformat()} jest z przyszłości: biała lista podaje stan najwyżej na dziś ({today.isoformat()})."
    try:
        earliest = today.replace(year=today.year - MAX_YEARS_BACK)
    except ValueError:  # 29 February
        earliest = today.replace(year=today.year - MAX_YEARS_BACK, day=28)
    if on < earliest:
        return f"Biała lista udostępnia stan najwyżej {MAX_YEARS_BACK} lat wstecz (od {earliest.isoformat()})."
    return None


# --------------------------------------------------------------------------- quota guard


def _key(endpoint: str) -> str:
    return f"{SOURCE_ID}.{endpoint}"


def quota_consume(store: Store, endpoint: str, *, now: datetime | None = None) -> tuple[bool, int, int]:
    """Count one call before sending it. Returns (allowed, used_today_after, limit); nothing is counted if refused."""
    today = warsaw_today(now)
    day, limit = today.isoformat(), LIMITS[endpoint]
    with store.batch():
        store.db.execute("INSERT OR IGNORE INTO registry_quota(day, endpoint, count) VALUES (?,?,0)", (day, _key(endpoint)))
        cur = store.db.execute("UPDATE registry_quota SET count = count + 1 WHERE day=? AND endpoint=? AND count < ?",
                               (day, _key(endpoint), limit))
        allowed = cur.rowcount == 1
        used = store.db.execute("SELECT count FROM registry_quota WHERE day=? AND endpoint=?",
                                (day, _key(endpoint))).fetchone()[0]
        store.db.execute("DELETE FROM registry_quota WHERE day < ?",
                         ((today - timedelta(days=QUOTA_KEEP_DAYS)).isoformat(),))
    return allowed, used, limit


def quota_mark_exhausted(store: Store, endpoint: str, *, now: datetime | None = None) -> None:
    """After an upstream quota refusal: no more calls to this endpoint until midnight (Polish time)."""
    day = warsaw_today(now).isoformat()
    with store.batch():
        store.db.execute("INSERT OR IGNORE INTO registry_quota(day, endpoint, count) VALUES (?,?,0)", (day, _key(endpoint)))
        store.db.execute("UPDATE registry_quota SET count = MAX(count, ?) WHERE day=? AND endpoint=?",
                         (LIMITS[endpoint], day, _key(endpoint)))


def quota_status(store: Store, *, now: datetime | None = None) -> dict[str, Any]:
    """Today's usage, e.g. for sources_status: {'day', 'search': {used, limit, upstream_limit}, 'check': {...}}."""
    day = warsaw_today(now).isoformat()
    out: dict[str, Any] = {"day": day, "timezone": "Europe/Warsaw"}
    for ep in LIMITS:
        row = store.db.execute("SELECT count FROM registry_quota WHERE day=? AND endpoint=?", (day, _key(ep))).fetchone()
        out[ep] = {"used": row[0] if row else 0, "limit": LIMITS[ep], "upstream_limit": UPSTREAM_LIMITS[ep]}
    return out


@contextmanager
def no_retry(client: PoliteClient) -> Iterator[PoliteClient]:
    """White-list calls are never retried (each try counts against the daily quota)."""
    old = client.max_retries
    client.max_retries = 0
    try:
        yield client
    finally:
        client.max_retries = old


# --------------------------------------------------------------------------- privacy


_POSTCODE_TOWN = re.compile(r"\b\d{2}-\d{3}\s+(.+?)\s*$")


def is_natural_person(subject: dict) -> bool:
    """Conservative: a PESEL, or no KRS number (sole traders, civil-law partnerships, some public bodies)."""
    return bool(subject.get("pesel")) or not subject.get("krs")


def town_of(address: Any) -> str | None:
    """Town from 'UL. X 1, 00-001 MIASTO' (text after the postcode); None if there is no postcode."""
    m = _POSTCODE_TOWN.search(address) if isinstance(address, str) else None
    return m.group(1) if m else None


def _names(people: Any) -> list[str]:
    out = []
    for p in people or []:
        if not isinstance(p, dict):
            continue
        name = p.get("companyName") or " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x)
        if name:
            out.append(name)
    return out


_DATES = ("registrationLegalDate", "registrationDenialDate", "restorationDate", "removalDate", "exemptionSmeDate")
_BASES = ("registrationDenialBasis", "restorationBasis", "removalBasis")


def _snake(k: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()


def public_view(subject: dict) -> dict[str, Any]:
    """Privacy-filtered subject. Never contains PESEL, bank accounts or a natural person's address/REGON."""
    accounts = subject.get("accountNumbers") or []
    common = {_snake(k): subject.get(k) for k in _DATES + _BASES}
    if is_natural_person(subject):
        return {
            "subject_type": "natural_person_or_not_in_krs",
            "name": subject.get("name"), "nip": subject.get("nip"), "status_vat": subject.get("statusVat"),
            "town": town_of(subject.get("workingAddress")) or town_of(subject.get("residenceAddress")),
            "account_count": len(accounts), **common,
        }
    return {
        "subject_type": "entity_in_krs",
        "name": subject.get("name"), "nip": subject.get("nip"), "regon": subject.get("regon"),
        "krs": subject.get("krs"), "status_vat": subject.get("statusVat"),
        "working_address": subject.get("workingAddress"), "residence_address": subject.get("residenceAddress"),
        **common,
        "representatives": _names(subject.get("representatives")),
        "authorized_clerks": _names(subject.get("authorizedClerks")),
        "partners": _names(subject.get("partners")),
        "account_count": len(accounts), "has_virtual_accounts": subject.get("hasVirtualAccounts"),
    }


# --------------------------------------------------------------------------- calls


@dataclass
class WlAnswer:
    outcome: SourceOutcome
    view: dict[str, Any] | None = None
    natural_person: bool = False
    krs: str | None = None  # for chaining to KRS (never set for natural persons)
    nip: str | None = None


def search_url(kind: str, value: str, on: date) -> str:
    return f"{BASE}/search/{kind}/{value}?date={on.isoformat()}"


def check_url(kind: str, value: str, nrb: str, on: date) -> str:
    return f"{BASE}/check/{kind}/{value}/bank-account/{nrb}?date={on.isoformat()}"


def _quota_refused(out: SourceOutcome, endpoint: str, used: int, limit: int) -> None:
    out.status = SourceStatus.daily_quota_exhausted
    out.detail = (f"Dzienny limit zapytań '{endpoint}' do białej listy wyczerpany po stronie serwera ({used}/{limit}; "
                  f"limit MF {UPSTREAM_LIMITS[endpoint]}/dzień). Zapytania nie wysłano; limit odnawia się o północy "
                  "czasu polskiego. Przekroczenie limitu MF blokuje też wyszukiwarkę na podatki.gov.pl.")


def _call(store: Store, client: PoliteClient, url: str, endpoint: str,
          out: SourceOutcome) -> tuple[dict, FetchResult] | None:
    """Count, send once, parse `result`. On failure sets out.status/detail and returns None."""
    allowed, used, limit = quota_consume(store, endpoint)
    out.extra["quota"] = {"endpoint": endpoint, "used_today": used, "limit": limit,
                          "upstream_limit": UPSTREAM_LIMITS[endpoint], "day": warsaw_today().isoformat()}
    if not allowed:
        _quota_refused(out, endpoint, used, limit)
        return None
    out.requests += 1
    try:
        with no_retry(client):
            r: FetchResult = client.get(url, accept=ACCEPT)
    except UpstreamError as e:
        if e.http_status == 429:
            quota_mark_exhausted(store, endpoint)
            out.status = SourceStatus.daily_quota_exhausted
            out.detail = "MF odrzuciło zapytanie (HTTP 429), najpewniej z powodu limitu dziennego; blokada do północy."
        else:
            out.status = SourceStatus.source_unavailable
            out.detail = f"Biała lista niedostępna: {e.reason} (HTTP {e.http_status}). Nie wiadomo nic o podmiocie."
        return None
    out.fetched_at = r.fetched_at
    body: Any = None
    try:
        body = json.loads(r.content)
        result = body["result"]
        if not isinstance(result, dict):
            raise TypeError("result")
    except (ValueError, KeyError, TypeError) as e:
        code = body.get("code") if isinstance(body, dict) else None
        out.status = SourceStatus.source_unavailable
        out.detail = f"Nieoczekiwana odpowiedź białej listy ({code or type(e).__name__})."
        return None
    return result, r


def search(store: Store, client: PoliteClient, kind: str, value: str, on: date) -> WlAnswer:
    """Search one subject by NIP or REGON on a date (1 of the daily 'search' quota)."""
    url = search_url(kind, value, on)
    out = SourceOutcome(SOURCE_ID, SourceStatus.ok, url=url, state_as_of=on.isoformat(), processing=PROCESSING,
                        attribution=ATTRIBUTION, extra={"call": f"search/{kind}"})
    got = _call(store, client, url, "search", out)
    if got is None:
        return WlAnswer(out)
    result, r = got
    subject = result.get("subject")
    req = {"request_id": result.get("requestId"), "request_datetime": result.get("requestDateTime"),
           "on_date": on.isoformat()}
    out.extra.update(req)
    if not subject:
        out.status = SourceStatus.not_found
        out.detail = (f"Brak podmiotu o tym numerze {kind.upper()} w wykazie podatników VAT na dzień {on.isoformat()}. "
                      "Wykaz obejmuje podatników VAT (także wykreślonych i tych, którym odmówiono rejestracji), "
                      "więc brak wpisu nie oznacza, że podmiot nie istnieje.")
        snap = store.put_snapshot(SOURCE_ID, r.url, r.content, r.content_type, parser_version=PARSER_VERSION,
                                  fetched_at=r.fetched_at)
        out.snapshot_id, out.stored = snap.snapshot_id, True
        return WlAnswer(out, view=req)
    natural = is_natural_person(subject)
    view = {**public_view(subject), **req}
    if natural:  # data minimisation: keep only what we return, plus the request id
        content = json.dumps({"result": {"subject": public_view(subject), "requestId": result.get("requestId"),
                                         "requestDateTime": result.get("requestDateTime")},
                              "_prawnik_mcp": "minimised copy: possible natural person; raw answer not stored"},
                             ensure_ascii=False).encode()
        snap = store.put_snapshot(SOURCE_ID, r.url, content, "application/json; profile=minimised",
                                  parser_version=PARSER_VERSION, fetched_at=r.fetched_at)
        out.processing = PROCESSING + "; zapisano tylko zminimalizowaną kopię odpowiedzi"
    else:
        snap = store.put_snapshot(SOURCE_ID, r.url, r.content, r.content_type, parser_version=PARSER_VERSION,
                                  fetched_at=r.fetched_at)
    out.snapshot_id, out.stored = snap.snapshot_id, True
    return WlAnswer(out, view=view, natural_person=natural, krs=None if natural else subject.get("krs"),
                    nip=subject.get("nip"))


def check_account(store: Store, client: PoliteClient, kind: str, value: str, nrb: str, on: date) -> tuple[SourceOutcome, dict]:
    """Is the account on the list for this subject on this date? (1 of the daily 'check' quota)."""
    url = check_url(kind, value, nrb, on)
    shown = url.replace(nrb, mask_account(nrb))
    out = SourceOutcome(SOURCE_ID, SourceStatus.ok, url=shown, state_as_of=on.isoformat(), processing=PROCESSING,
                        attribution=ATTRIBUTION, extra={"call": f"check/{kind}"})
    view: dict[str, Any] = {"account": mask_account(nrb), "on_date": on.isoformat()}
    got = _call(store, client, url, "check", out)
    if got is None:
        return out, view
    result, r = got
    assigned = result.get("accountAssigned")
    if assigned not in ("TAK", "NIE"):
        out.status = SourceStatus.source_unavailable
        out.detail = f"Nieoczekiwana wartość accountAssigned: {assigned!r}."
        return out, view
    view.update({"account_assigned": assigned, "request_id": result.get("requestId"),
                 "request_datetime": result.get("requestDateTime")})
    out.extra.update({k: view[k] for k in ("request_id", "request_datetime")})
    content = r.content if nrb.encode() not in r.content else json.dumps(
        {"result": {k: result.get(k) for k in ("accountAssigned", "requestDateTime", "requestId")}}).encode()
    snap = store.put_snapshot(SOURCE_ID, shown, content, r.content_type, parser_version=PARSER_VERSION,
                              fetched_at=r.fetched_at)
    out.snapshot_id, out.stored = snap.snapshot_id, True
    return out, view
