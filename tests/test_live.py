"""Hybrid access: live search, cache, time budget and lazy fetch — offline, with a mocked transport."""

import json
import time
from pathlib import Path

import httpx
import pytest

from prawnik_mcp import live, service
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.store import Store

FX = Path(__file__).parent / "fixtures" / "raw"
ELI_SEARCH = {  # shape of api.sejm.gov.pl/eli/acts/search (real act metadata)
    "count": 1, "totalCount": 1, "offset": 0, "items": [{
        "ELI": "DU/2018/1000", "address": "WDU20180001000", "displayAddress": "Dz.U. 2018 poz. 1000",
        "title": "Ustawa z dnia 10 maja 2018 r. o ochronie danych osobowych", "type": "Ustawa",
        "status": "akt posiada tekst jednolity", "inForce": "IN_FORCE", "promulgation": "2018-05-24"}]}


@pytest.fixture
def mock_live(monkeypatch):
    calls: list[str] = []
    state = {"eli_delay": 0.0}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        if "saos.org.pl/api/search/judgments" in url:
            return httpx.Response(200, content=(FX / "saos_search_odstapienie.json").read_bytes())
        if "saos.org.pl/api/judgments/31345" in url:
            return httpx.Response(200, content=(FX / "saos_31345.json").read_bytes())
        if "saos.org.pl/api/judgments/" in url:
            return httpx.Response(404)
        if "api.sejm.gov.pl/eli/acts/search" in url:
            time.sleep(state["eli_delay"])
            return httpx.Response(200, json=ELI_SEARCH)
        return httpx.Response(404)

    monkeypatch.setenv("PRAWNIK_MCP_OFFLINE", "0")
    monkeypatch.setattr(live, "CLIENT_FACTORY",
                        lambda: PoliteClient(transport=httpx.MockTransport(handler), min_delay=0, sleep=lambda s: None))
    return calls, state


def test_live_search_on_empty_store_then_cache(tmp_path, mock_live):
    calls, _ = mock_live
    store = Store(tmp_path)
    r = service.search_legal(store, "odstąpienie od umowy zawartej na odległość")
    assert r.status.value == "ok"
    origins = {h["metadata"]["origin"] for h in r.data["hits"]}
    assert origins == {"live"}
    assert any(h["document_id"] == "saos:31345" for h in r.data["hits"])
    assert all(h["snapshot_id"] == "" for h in r.data["hits"])  # not stored: must be fetched before quoting
    def cached_sources(cs):
        return [c for c in cs if "saos.org.pl" in c or "api.sejm.gov.pl" in c]

    n = len(cached_sources(calls))
    r2 = service.search_legal(store, "odstąpienie od umowy zawartej na odległość")
    assert {h["metadata"]["origin"] for h in r2.data["hits"]} == {"cache"}
    assert len(cached_sources(calls)) == n  # successful sources served from cache; failed ones are retried


def test_live_false_and_offline_env_make_no_requests(tmp_path, mock_live, monkeypatch):
    calls, _ = mock_live
    store = Store(tmp_path)
    r = service.search_legal(store, "odstąpienie od umowy", live=False)
    assert r.status.value == "source_unavailable" and not calls
    monkeypatch.setenv("PRAWNIK_MCP_OFFLINE", "1")
    service.search_legal(store, "odstąpienie od umowy")
    service.get_legal_document(store, "saos:31345")
    assert not calls


def test_time_budget_reports_slow_source(tmp_path, mock_live):
    _, state = mock_live
    state["eli_delay"] = 1.0
    store = Store(tmp_path)
    hits, warnings, unavailable = live.live_search(store, "ochrona danych", kinds=None, filters=None, limit=3,
                                                   budget_s=0.3)
    assert "eli" in unavailable and any("eli" in w for w in warnings)
    assert {sid for sid, _, _ in hits} == {"saos"}


def test_lazy_fetch_stores_judgment_with_snapshot(tmp_path, mock_live):
    store = Store(tmp_path)
    r = service.get_legal_document(store, "saos:31345")
    assert r.status.value == "ok"
    assert any("pobrano ze źródła" in w for w in r.warnings)
    assert r.data["case_numbers"] == ["I ACa 772/13"]
    assert store.get_judgment("saos:31345").snapshot_id.startswith("saos:")
    missing = service.get_legal_document(store, "saos:999")
    assert missing.status.value == "not_found" and any("404" in w and "nie dowodzi" in w for w in missing.warnings)


def test_case_number_falls_back_to_live_saos(tmp_path, mock_live):
    calls, _ = mock_live
    store = Store(tmp_path)
    r = service.search_legal(store, "I ACa 772/13")
    assert r.status.value in ("ok", "ambiguous")
    assert any("caseNumber=I+ACa+772%2F13" in c or "caseNumber=I%20ACa" in c for c in calls)
    assert any("na żywo" in w for w in r.warnings)


def test_identifier_resolution():
    assert service._resolve_act("art. 6 RODO") == "celex:32016R0679"
    assert service._resolve_act("art. 5 Dz.U. 2024 poz. 1061") == "eli:DU/2024/1061"
    assert service._resolve_act("DU/1964/93 art. 471") == "eli:DU/1964/93"
    assert service._resolve_act("art. 5 dyrektywy 93/13/EWG") == "celex:31993L0013"
    assert json.dumps(service._resolve_act("art. 27 upk")) == '"eli:DU/2014/827"'
