"""Entity card from KRS, the VAT white list and VIES — live, on demand, with per-source provenance.

Paths and request cost (the white-list 'search' quota matters most: 100/day at MF, 80/day here):
- NIP:   white-list search (1 search) -> KRS current extract when the subject has a KRS number (1–2 GETs);
- REGON: white-list search by REGON (1 search) -> KRS as above;
- KRS:   KRS current extract (1–2 GETs: register P, then S) -> white-list search by the extract's NIP
         (1 search; `include_vat=False` skips it and saves the quota);
- EU VAT number: VIES (1 POST); a PL number also runs the NIP path;
- `bank_account`: + white-list check of that account for the NIP (1 of the 'check' quota, 5000/day at MF);
- `include_full_history`: + KRS full extract (1 GET). A struck-off entity (KRS answers 204) always costs the
  full extract: its card is the register state before the deletion entry.
There is no NIP -> KRS lookup in the KRS API, so a NIP reaches KRS only through the white list.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Any

from prawnik_mcp import sources
from prawnik_mcp.connectors.http import PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import Coverage, ResultStatus, ToolResult
from prawnik_mcp.registries import SourceOutcome, SourceStatus, ids, krs, vies, wl_vat
from prawnik_mcp.store import Store

HOSTS = {"krs": krs.HOST, "wl_vat": wl_vat.HOST, "vies": vies.HOST}
DEFAULT_RATE = {"krs": 0.5, "wl_vat": 1.0, "vies": 1.0}  # used when the catalog has no entry for the source
RETRIES = {"krs": 1, "wl_vat": 0, "vies": 1}
SOURCE_ORDER = ("krs", "wl_vat", "vies")

CORPUS_NOTE = ("Dane pobrane na żywo z rejestrów publicznych, nie z lokalnego korpusu. Brak wpisu w jednym rejestrze "
               "nie oznacza, że podmiot nie istnieje.")
W_KRS_OFFICIAL = "Odpis z API KRS nie zastępuje urzędowego odpisu z KRS (dokumentu pobranego z Centralnej Informacji KRS)."
W_NO_FLAGS = "Brak wpisów o likwidacji, upadłości lub restrukturyzacji nie jest potwierdzeniem kondycji podmiotu."
W_MASKED = ("Imiona, nazwiska i PESEL w odpisie KRS są maskowane przez Ministerstwo Sprawiedliwości i tak zwracane; "
            "w polach opisowych (np. rodzaj prokury) usunięto numery PESEL i zamaskowano nazwiska.")
W_SELF_MASKED = "Część nazwisk przyszła z API KRS bez maskowania; zamaskowano je po stronie prawnik-mcp."
W_NATURAL = ("Podmiot z białej listy ma PESEL albo nie ma numeru KRS: traktowany jak (możliwa) osoba fizyczna. Zwrócono "
             "tylko nazwę, NIP, status VAT, miejscowość, liczbę rachunków i daty; bez adresu, rachunków i PESEL.")


class _Clients:
    """One shared client (must tolerate max_retries=0 for white-list calls), or per-source clients built here."""

    def __init__(self, client: PoliteClient | None):
        self.shared = client
        self.own: dict[str, PoliteClient] = {}

    def get(self, source_id: str) -> PoliteClient:
        if self.shared is not None:
            return self.shared
        if source_id not in self.own:
            host, info = HOSTS[source_id], sources.catalog().get(source_id)
            delay = info.min_delay if info else 1.0 / DEFAULT_RATE[source_id]
            self.own[source_id] = PoliteClient(allowlist={host}, host_delays={host: delay},
                                               max_retries=RETRIES[source_id], timeout=30.0)
        return self.own[source_id]

    def close(self) -> None:
        for c in self.own.values():
            c.close()


def _outcome(source_id: str, status: SourceStatus, call: str, detail: str | None = None, **kw: Any) -> SourceOutcome:
    proc = {"krs": krs.PROCESSING, "wl_vat": wl_vat.PROCESSING, "vies": vies.PROCESSING}[source_id]
    attr = {"krs": krs.ATTRIBUTION, "wl_vat": wl_vat.ATTRIBUTION, "vies": vies.ATTRIBUTION}[source_id]
    return SourceOutcome(source_id, status, processing=proc, attribution=attr, detail=detail,
                         extra={"call": call}, **kw)


class _Lookup:
    def __init__(self, store: Store, clients: _Clients, on: Date, explicit_date: Date | None, nrb: str | None,
                 requester: tuple[str, str] | None, include_full_history: bool, include_vat: bool):
        self.store, self.clients, self.on, self.explicit_date = store, clients, on, explicit_date
        self.nrb, self.requester = nrb, requester
        self.include_full_history, self.include_vat = include_full_history, include_vat
        self.outcomes: list[SourceOutcome] = []
        self.entity: dict | None = None
        self.vat: dict | None = None
        self.vies: dict | None = None
        self.history: dict | None = None
        self.natural_person = False
        self.self_masked = False

    # ------------------------------------------------------------------ VIES
    def run_vies(self, country: str, number: str) -> None:
        ans = vies.check_vat(self.clients.get("vies"), country, number, self.requester)
        self.outcomes.append(ans.outcome)
        self.vies = ans.view

    # ------------------------------------------------------------------ white list
    def wl_search(self, kind: str, value: str) -> wl_vat.WlAnswer:
        ans = wl_vat.search(self.store, self.clients.get("wl_vat"), kind, value, self.on)
        self.outcomes.append(ans.outcome)
        if ans.view is not None:
            self.vat = {**(self.vat or {}), **ans.view}
        self.natural_person |= ans.natural_person
        return ans

    def wl_check(self, kind: str | None, value: str | None, skip_reason: str | None = None) -> None:
        if not self.nrb:
            return
        if skip_reason or not kind or not value:
            out = _outcome("wl_vat", SourceStatus.skipped, "check", skip_reason or "Brak NIP/REGON do sprawdzenia rachunku.")
            view: dict[str, Any] = {"account": ids.mask_account(self.nrb)}
        else:
            out, view = wl_vat.check_account(self.store, self.clients.get("wl_vat"), kind, value, self.nrb, self.on)
        self.outcomes.append(out)
        self.vat = {**(self.vat or {}), "account_check": {**view, "status": out.status.value, "detail": out.detail,
                                                           "snapshot_id": out.snapshot_id}}

    # ------------------------------------------------------------------ KRS
    def krs_fetch(self, number: str, *, full: bool = False, registers: tuple[str, ...] = krs.REGISTERS) -> krs.KrsAnswer | None:
        call = "full_extract" if full else "current_extract"
        try:
            ans = krs.fetch_extract(self.store, self.clients.get("krs"), number, full=full, registers=registers)
        except SourceUnavailable as e:
            self.outcomes.append(_outcome("krs", SourceStatus.source_unavailable, call,
                                          f"API KRS niedostępne: {e.reason} (HTTP {e.http_status}). Nie wiadomo nic o podmiocie.",
                                          url=e.url, requests=getattr(e, "requests", 1)))
            return None
        out = _outcome("krs", SourceStatus.ok, call, url=ans.url, requests=ans.requests)
        out.extra["register"] = ans.register
        if ans.status == "not_found":
            out.status = SourceStatus.not_found
            out.detail = (f"API KRS: brak podmiotu o numerze {number} w rejestrze "
                          f"{' ani '.join(registers)} (HTTP 404).")
        elif ans.status == "struck_off":
            out.detail = ("API KRS: brak odpisu aktualnego (HTTP 204); taka odpowiedź była obserwowana dla podmiotów "
                          "wykreślonych. Dane z odpisu pełnego.")
        else:
            head = ans.head
            out.fetched_at, out.snapshot_id, out.stored = ans.fetch.fetched_at, ans.snapshot.snapshot_id, True
            out.state_as_of = krs.iso_date(head.get("stanZDnia"))
            out.extra.update({"stan_pozycji": head.get("stanPozycji"), "extract_datetime": head.get("dataCzasOdpisu")})
        self.outcomes.append(out)
        return ans

    def run_krs(self, number: str) -> None:
        ans = self.krs_fetch(number)
        if ans is None or ans.status == "not_found":
            return
        if ans.status == "ok":
            card, sm = krs.current_card(ans.odpis, ans.register)
            card["status_flags"]["struck_off"] = {
                "value": False, "basis": f"odpis aktualny dostępny (stanPozycji={ans.head.get('stanPozycji')})"}
            self.entity, self.self_masked = card, self.self_masked or sm
            if self.include_full_history:
                full = self.krs_fetch(number, full=True, registers=(ans.register,))
                if full and full.status == "ok":
                    self.history = krs.build_history(full.odpis, on=self.explicit_date)
            return
        # struck off: the full extract gives the state before the deletion entry
        full = self.krs_fetch(number, full=True, registers=(ans.register,))
        basis = "API KRS: brak odpisu aktualnego (HTTP 204)"
        if full and full.status == "ok":
            card, sm = krs.card_at_deletion(full.odpis, full.register)
            dele = krs.deletion_entry(full.odpis)
            card["status_flags"]["struck_off"] = {
                "value": True, "entry": dele,
                "basis": f"wpis nr {dele['no']} '{dele['description']}' z {dele['date']}" if dele else basis + "; "
                         "w odpisie pełnym nie znaleziono wpisu o wykreśleniu",
                "card_state": "stan rejestru przed wpisem o wykreśleniu"}
            self.entity, self.self_masked = card, self.self_masked or sm
            if self.include_full_history:
                self.history = krs.build_history(full.odpis, on=self.explicit_date)
        else:
            self.entity = {"krs": number, "register": ans.register,
                           "status_flags": {"struck_off": {"value": True, "basis": basis + "; odpis pełny niedostępny"}}}

    # ------------------------------------------------------------------ paths
    def path_wl_first(self, kind: str, value: str) -> None:
        ans = self.wl_search(kind, value)
        st = ans.outcome.status
        nip = ans.nip or (value if kind == "nip" else None)
        if st == SourceStatus.not_found:
            self.wl_check(None, None, f"Podmiot nie figuruje w wykazie na dzień {self.on.isoformat()}.")
        else:
            self.wl_check("nip" if nip else kind, nip or value)
        if ans.krs:
            norm = ids.normalize_krs(str(ans.krs))
            if norm:
                self.run_krs(norm)
                return
        reason = {
            SourceStatus.not_found: "Brak podmiotu w białej liście; numeru KRS nie ustalono (API KRS nie wyszukuje po NIP).",
            SourceStatus.ok: "Biała lista nie podaje numeru KRS (osoba fizyczna albo podmiot spoza KRS).",
        }.get(st, "Biała lista nie odpowiedziała; numeru KRS nie ustalono (API KRS nie wyszukuje po NIP).")
        self.outcomes.append(_outcome("krs", SourceStatus.skipped, "current_extract", reason))

    def path_krs_first(self, number: str) -> None:
        self.run_krs(number)
        nip = (self.entity or {}).get("nip")
        if not nip:
            why = "Odpis KRS nie zawiera NIP." if self.entity else "Nie ustalono NIP (brak odpisu KRS)."
            self.outcomes.append(_outcome("wl_vat", SourceStatus.skipped, "search/nip", why))
            self.wl_check(None, None, why)
            return
        if self.include_vat:
            ans = self.wl_search("nip", nip)
            if ans.krs and ids.normalize_krs(str(ans.krs)) != number:
                self.vat = {**(self.vat or {}), "krs_mismatch": True}
        else:
            self.outcomes.append(_outcome("wl_vat", SourceStatus.skipped, "search/nip",
                                          "Pominięto na żądanie (include_vat=False); limit wyszukiwań nienaruszony."))
        self.wl_check("nip", nip)


# --------------------------------------------------------------------------- result


def _top_status(outcomes: list[SourceOutcome]) -> ResultStatus:
    st = [o.status for o in outcomes if o.status != SourceStatus.skipped]
    if SourceStatus.ok in st:
        return ResultStatus.ok
    if st and all(s == SourceStatus.not_found for s in st):
        return ResultStatus.not_found
    if SourceStatus.daily_quota_exhausted in st:
        return ResultStatus.blocked
    if SourceStatus.invalid_input in st:
        return ResultStatus.invalid_input
    return ResultStatus.source_unavailable


def _summary(run: _Lookup) -> dict[str, Any]:
    e, v, s = run.entity or {}, run.vat or {}, run.vies or {}
    out: dict[str, Any] = {}
    for key, vals in {
        "name": ((e.get("name"), "krs"), (v.get("name"), "wl_vat"), (s.get("name"), "vies")),
        "legal_form": ((e.get("legal_form"), "krs"),),
        "krs": ((e.get("krs"), "krs"), (v.get("krs"), "wl_vat")),
        "nip": ((e.get("nip"), "krs"), (v.get("nip"), "wl_vat")),
        "regon": ((e.get("regon"), "krs"), (v.get("regon"), "wl_vat")),
        "status_vat": ((v.get("status_vat"), "wl_vat"),),
        "struck_off_krs": (((e.get("status_flags") or {}).get("struck_off", {}).get("value"), "krs"),),
        "vies_valid": ((s.get("valid"), "vies"),),
    }.items():
        for val, src in vals:
            if val is not None:
                out[key] = {"value": val, "source": src}
                break
    return out


def _warnings(run: _Lookup) -> list[str]:
    w: list[str] = []
    ok = {o.source_id for o in run.outcomes if o.status == SourceStatus.ok}
    krs_out = next((o for o in run.outcomes if o.source_id == "krs" and o.extra.get("call") == "current_extract"
                    and o.status == SourceStatus.ok), None)
    if "krs" in ok and run.entity:
        w.append(W_KRS_OFFICIAL + (f" Stan odpisu na {krs_out.state_as_of}." if krs_out and krs_out.state_as_of else ""))
        w += [W_NO_FLAGS, W_MASKED]
        if run.self_masked:
            w.append(W_SELF_MASKED)
        if run.explicit_date and krs_out and not run.history:
            w.append(f"Parametr date ({run.explicit_date.isoformat()}) dotyczy białej listy; odpis KRS jest aktualny "
                     f"(stan na {krs_out.state_as_of}). Stan KRS na dzień: include_full_history=true.")
    e, v = run.entity or {}, run.vat or {}
    if e.get("nip") and v.get("nip") and e["nip"] != v["nip"]:
        w.append(f"NIP w KRS ({e['nip']}) różni się od NIP w białej liście ({v['nip']}).")
    if v.get("krs_mismatch"):
        w.append("Numer KRS w białej liście różni się od numeru, o który pytano.")
    if run.natural_person:
        w.append(W_NATURAL)
    if any(o.source_id == "wl_vat" and o.status == SourceStatus.ok for o in run.outcomes):
        w.append(wl_vat.REQUEST_ID_NOTE)
        q = next((o.extra.get("quota") for o in run.outcomes if o.extra.get("call", "").startswith("search")
                  and o.extra.get("quota")), None)
        if q:
            w.append(f"Wyszukiwanie w białej liście zużywa dzienny limit: dziś {q['used_today']}/{q['limit']} po stronie "
                     f"serwera (limit MF {q['upstream_limit']}; po jego przekroczeniu MF blokuje dostęp do północy, "
                     "także w wyszukiwarce www).")
    if run.vies is not None or any(o.source_id == "vies" for o in run.outcomes):
        w.append(vies.TERMS_NOTE)
    for o in run.outcomes:
        if o.status in (SourceStatus.source_unavailable, SourceStatus.daily_quota_exhausted) and o.detail:
            w.append(f"{o.source_id}: {o.detail}")
    return w


def _parse_date(v: Any) -> Date | None:
    if v is None or v == "":
        return None
    if isinstance(v, Date):
        return v
    return Date.fromisoformat(str(v).strip()[:10])


def lookup_entity(store: Store, identifier: str, *, date: str | Date | None = None, bank_account: str | None = None,
                  requester_vat: str | None = None, include_full_history: bool = False, include_vat: bool = True,
                  client: PoliteClient | None = None) -> ToolResult:
    """Entity card for a NIP, REGON, KRS number or EU VAT number (see the module docstring for paths and cost).

    date: state date for the VAT white list (YYYY-MM-DD; default today in Poland; not future, <= 5 years back);
      with include_full_history it also gives the KRS register state on that date.
    bank_account: Polish account (26 digits, PL prefix and spaces allowed) checked against the white list for
      the entity (TAK/NIE + requestId); never listed back in full.
    requester_vat: the caller's own EU VAT number, sent to VIES to obtain a consultation number.
    include_full_history: also fetch the KRS full extract (history of names, seats, board, prokura).
    include_vat: KRS path only — False skips the white-list search (saves the daily search quota).
    client: one PoliteClient for all calls (tests/integration). It is used with max_retries=0 for white-list
      calls. Default: per-source clients restricted to the registry hosts.
    """
    cls = ids.classify(identifier)
    if not cls.ok:
        return ToolResult(status=ResultStatus.invalid_input, warnings=[cls.reason or "Nieprawidłowy identyfikator."],
                          data={"query": {"identifier_kind": None}}, coverage=Coverage(corpus_note=CORPUS_NOTE))
    if cls.kind == "nrb":
        return ToolResult(status=ResultStatus.invalid_input, coverage=Coverage(corpus_note=CORPUS_NOTE), warnings=[
            "Numer rachunku nie identyfikuje podmiotu w tym narzędziu (nie ma wyszukiwania właściciela rachunku). "
            "Podaj NIP podmiotu i rachunek w bank_account."], data={"query": {"identifier_kind": "nrb"}})
    problems: list[str] = []
    try:
        explicit = _parse_date(date)
    except ValueError:
        explicit, problems = None, ["date: oczekiwany format YYYY-MM-DD."]
    today = wl_vat.warsaw_today()
    on = explicit or today
    uses_wl = cls.kind in ("nip", "regon") or (cls.kind == "eu_vat" and cls.country == "PL") or (
        cls.kind == "krs" and (include_vat or bank_account))
    if uses_wl and not problems and (err := wl_vat.date_error(on, today)):
        problems.append(err)
    nrb = None
    if bank_account:
        nrb = ids.normalize_nrb(bank_account)
        if not nrb:
            problems.append("bank_account: nieprawidłowy numer rachunku (26 cyfr, opcjonalnie z PL; kontrola mod 97).")
        elif cls.kind == "eu_vat" and cls.country != "PL":
            problems.append("bank_account: sprawdzenie rachunku w białej liście dotyczy tylko podmiotów polskich.")
    requester = None
    if requester_vat:
        rq = ids.classify(requester_vat)
        if rq.kind != "eu_vat":
            problems.append("requester_vat: oczekiwany numer VAT UE z prefiksem kraju (np. PL1234563218).")
        else:
            requester = (rq.country or "", rq.value or "")
    query = {"identifier_kind": cls.kind, "normalized": cls.vat_id or cls.value, "note": cls.note,
             "date": on.isoformat(), "date_given": explicit is not None,
             "bank_account": ids.mask_account(nrb) if nrb else None,
             "requester_vat": f"{requester[0]}{requester[1]}" if requester else None,
             "include_full_history": include_full_history, "include_vat": include_vat}
    if problems:
        return ToolResult(status=ResultStatus.invalid_input, warnings=problems, data={"query": query},
                          coverage=Coverage(corpus_note=CORPUS_NOTE))
    if client is None:
        from prawnik_mcp.live import live_enabled

        if not live_enabled():
            return ToolResult(status=ResultStatus.source_unavailable, data={"query": query},
                              coverage=Coverage(corpus_note=CORPUS_NOTE),
                              warnings=["Tryb offline (PRAWNIK_MCP_OFFLINE=1): rejestrów nie można odpytać."])
    clients = _Clients(client)
    run = _Lookup(store, clients, on, explicit, nrb, requester, include_full_history, include_vat)
    try:
        if cls.kind == "eu_vat":
            run.run_vies(cls.country or "", cls.value or "")
            if cls.country == "PL":
                run.path_wl_first("nip", cls.value or "")
        elif cls.kind in ("nip", "regon"):
            run.path_wl_first(cls.kind, cls.value or "")
        else:
            run.path_krs_first(cls.value or "")
    finally:
        clients.close()

    by_source: dict[str, str] = {}
    for sid in SOURCE_ORDER:
        first = next((o for o in run.outcomes if o.source_id == sid and o.extra.get("call") != "check"), None)
        by_source[sid] = first.status.value if first else SourceStatus.skipped.value
    searched = [s for s in SOURCE_ORDER if any(o.source_id == s and o.status in (SourceStatus.ok, SourceStatus.not_found)
                                               for o in run.outcomes)]
    unavailable = [s for s in SOURCE_ORDER if s not in searched and any(
        o.source_id == s and o.status in (SourceStatus.source_unavailable, SourceStatus.daily_quota_exhausted)
        for o in run.outcomes)]
    cost: dict[str, int] = {}
    for o in run.outcomes:
        key = o.source_id if o.source_id != "wl_vat" else f"wl_vat_{o.extra.get('call', '').split('/')[0]}"
        cost[key] = cost.get(key, 0) + o.requests
    data = {
        "query": query,
        "summary": _summary(run),
        "entity": run.entity,
        "vat": run.vat,
        "vies": run.vies,
        "history": run.history,
        "source_status": by_source,
        "sources": [o.as_dict() for o in run.outcomes],
        "requests_made": cost,
        "wl_quota": wl_vat.quota_status(store) if any(o.source_id == "wl_vat" for o in run.outcomes) else None,
    }
    return ToolResult(status=_top_status(run.outcomes), data=data, warnings=_warnings(run),
                      coverage=Coverage(sources_searched=searched, sources_unavailable=unavailable, corpus_note=CORPUS_NOTE))
