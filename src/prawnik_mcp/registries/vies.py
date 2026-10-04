"""VIES (VAT Information Exchange System) of the European Commission: validity of an EU VAT number.

Endpoints (REST, no key; verified 2026-10-04):
- POST https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number
  JSON {"countryCode", "vatNumber", optional "requesterMemberStateCode", "requesterNumber"}
  -> valid, name, address, requestDate, requestIdentifier (consultation number; only when a requester is
  given), userError (VALID, INVALID, MS_UNAVAILABLE, …). Member states that do not disclose name/address
  answer "---".
- GET https://ec.europa.eu/taxation_customs/vies/rest-api/check-status: availability per member state.
Greece is EL.

Terms (VIES disclaimer, read 2026-10-04 from the site's own text): the site exists to let persons involved
in intra-Community supplies confirm the validity of a VAT number (art. 31 of Regulation (EU) 904/2010);
"any retransmission of the contents of this site … as well as any more general use other than as far as is
necessary to support the activity of a legitimate user … is expressly forbidden. In addition, any copying or
reproduction of the contents of this site is strictly forbidden." Therefore a VIES answer is returned to the
caller for one check and is never cached, stored as a snapshot, logged or recorded as a fixture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prawnik_mcp.connectors.http import PoliteClient, UpstreamError
from prawnik_mcp.registries import SourceOutcome, SourceStatus

SOURCE_ID = "vies"
HOST = "ec.europa.eu"
CHECK_URL = f"https://{HOST}/taxation_customs/vies/rest-api/check-vat-number"
STATUS_URL = f"https://{HOST}/taxation_customs/vies/rest-api/check-status"
ACCEPT = "application/json"
PROCESSING = "odpowiedź VIES przekazana bez zapisu, bez buforowania i bez kopii lokalnej (warunki VIES)"
ATTRIBUTION = "Źródło: VIES – Komisja Europejska (ec.europa.eu/taxation_customs/vies)"
TERMS_NOTE = ("VIES służy wyłącznie do potwierdzenia ważności numeru VAT UE w obrocie wewnątrzwspólnotowym; "
              "wynik nie jest zapisywany ani buforowany, a kopiowanie i dalsze rozpowszechnianie treści VIES jest "
              "zabronione (zastrzeżenia Komisji Europejskiej).")
UNAVAILABLE = {"MS_UNAVAILABLE", "MS_MAX_CONCURRENT_REQ", "GLOBAL_MAX_CONCURRENT_REQ", "SERVICE_UNAVAILABLE",
               "TIMEOUT", "VAT_BLOCKED", "IP_BLOCKED"}
INPUT_ERRORS = {"INVALID_INPUT", "INVALID_REQUESTER_INFO"}


@dataclass
class ViesAnswer:
    outcome: SourceOutcome
    view: dict[str, Any] | None = None


def _clean(v: Any) -> Any:
    return None if v in (None, "", "---") else v


def check_vat(client: PoliteClient, country: str, number: str, requester: tuple[str, str] | None = None) -> ViesAnswer:
    """One VIES check. `requester` = (member state code, number) of the checking trader, for a consultation number."""
    payload: dict[str, str] = {"countryCode": country, "vatNumber": number}
    if requester:
        payload.update({"requesterMemberStateCode": requester[0], "requesterNumber": requester[1]})
    out = SourceOutcome(SOURCE_ID, SourceStatus.ok, url=CHECK_URL, processing=PROCESSING, attribution=ATTRIBUTION,
                        stored=False, extra={"call": "check-vat-number"})
    out.requests = 1
    try:
        r = client.post(CHECK_URL, json=payload, accept=ACCEPT)
    except UpstreamError as e:
        out.status = SourceStatus.source_unavailable
        out.detail = f"VIES niedostępny: {e.reason} (HTTP {e.http_status}). Nie wiadomo nic o numerze."
        return ViesAnswer(out)
    out.fetched_at = r.fetched_at
    try:
        j = json.loads(r.content)
        if not isinstance(j, dict):
            raise TypeError("body")
    except (ValueError, TypeError):
        out.status = SourceStatus.source_unavailable
        out.detail = "Nieoczekiwana odpowiedź VIES (nie JSON)."
        return ViesAnswer(out)
    errors = [str(w.get("error")) for w in j.get("errorWrappers") or [] if isinstance(w, dict)]
    user_error = str(j.get("userError") or "")
    code = errors[0] if errors else user_error
    if errors or j.get("actionSucceed") is False or code in UNAVAILABLE | INPUT_ERRORS:
        out.status = SourceStatus.invalid_input if code in INPUT_ERRORS else SourceStatus.source_unavailable
        out.detail = f"VIES: {code or 'błąd'}" + (" (państwo członkowskie chwilowo niedostępne)" if code == "MS_UNAVAILABLE" else "")
        return ViesAnswer(out, view={"country_code": country, "vat_number": number, "user_error": code or None})
    if not isinstance(j.get("valid"), bool):
        out.status = SourceStatus.source_unavailable
        out.detail = "Odpowiedź VIES bez pola 'valid'."
        return ViesAnswer(out)
    view = {
        "country_code": j.get("countryCode") or country, "vat_number": j.get("vatNumber") or number,
        "valid": j["valid"], "name": _clean(j.get("name")), "address": _clean(j.get("address")),
        "request_date": j.get("requestDate"), "consultation_number": _clean(j.get("requestIdentifier")),
        "user_error": user_error or None,
        "note": None if j["valid"] else "VIES: numer nieaktywny dla transakcji wewnątrzwspólnotowych na dzień zapytania.",
    }
    return ViesAnswer(out, view=view)


def member_state_status(client: PoliteClient) -> dict[str, Any]:
    """Availability of the national VIES back-ends (not stored). Raises UpstreamError when VIES is down."""
    r = client.get(STATUS_URL, accept=ACCEPT)
    j = json.loads(r.content)
    return {"checked_at": datetime.now(UTC).isoformat(), "vow_available": (j.get("vow") or {}).get("available"),
            "countries": {c.get("countryCode"): c.get("availability") for c in j.get("countries") or []
                          if isinstance(c, dict)}}
