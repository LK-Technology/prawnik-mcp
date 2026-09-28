"""UODO connector (orzeczenia.uodo.gov.pl) — offline, httpx.MockTransport serving recorded responses."""

import re
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from prawnik_mcp.connectors.base import BulkLimits
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.connectors.uodo import UodoConnector
from prawnik_mcp.contracts import SourceKind
from prawnik_mcp.parsers.uodo import body_to_text, content_data_url, decode_turbo_stream, page_url, parse_content
from prawnik_mcp.store import Store

FX = Path(__file__).parent / "fixtures" / "raw" / "uodo"
HOST = "orzeczenia.uodo.gov.pl"
URN = "urn:ndoc:gov:pl:uodo:2022:dkn_5112_28"


def _data(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FX / name).read_bytes(), headers={"content-type": "text/x-script; charset=utf-8"})


def _client(calls: list) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        q = parse_qs(req.url.query.decode())
        calls.append((req.url.path, q))
        if req.url.path == "/search.data":
            if "rn" in q:
                return _data("search_rn.json")
            if "q" in q:
                return _data("search_sklep.json")
            return _data(f"search_window_p{q['page'][0]}.json")
        if m := re.fullmatch(r"/document/urn:ndoc:gov:pl:uodo:(\d{4}):([a-z0-9_]+)/content\.data", req.url.path):
            f = FX / f"content_{m.group(1)}_{m.group(2)}.json"
            return _data(f.name if f.exists() else "content_404.json")  # the portal answers 200 + empty document
        return httpx.Response(404, text="Not Found")

    return PoliteClient(transport=httpx.MockTransport(handler), allowlist={HOST}, min_delay=0, sleep=lambda s: None)


def test_search_maps_hits_and_case_numbers():
    calls: list = []
    hits = UodoConnector().search(_client(calls), "sklep internetowy", limit=4)
    assert calls[0] == ("/search.data", {"dcr": ["rodo"], "q": ["sklep internetowy"], "page": ["1"]})
    assert [h.document_id for h in hits] == ["uodo:2022:dkn_5112_27", "uodo:2021:dkn_5131_14",
                                             "uodo:2019:zspr_421_2", "uodo:2019:zspr_405_67"]
    h = hits[0]
    assert h.kind == "decision" and h.original_url == page_url("urn:ndoc:gov:pl:uodo:2022:dkn_5112_27")
    assert h.metadata["case_numbers"] == ["DKN.5112.27.2022"] and h.metadata["judgment_date"] == "2025-11-25"
    assert h.title.startswith("Decyzja Prezesa UODO nr DKN.5112.27.2022")
    assert h.snippet.startswith("nałożenie administracyjnej kary pieniężnej") and h.metadata["total"] == 7
    assert "nałożenie kary" in h.metadata["keywords"]
    assert hits[3].metadata["case_numbers"] == ["ZSPR.421.2.2019", "ZSPR.405.67.2019"]


def test_search_by_case_number_and_local_date_filter():
    calls: list = []
    conn = UodoConnector()
    hits = conn.search(_client(calls), "DKN.5112.28.2022", filters={"case_number": "dkn.5112.28.2022"})
    assert calls[0][1]["rn"] == ["DKN.5112.28.2022"] and "q" not in calls[0][1]
    assert [h.document_id for h in hits] == ["uodo:2022:dkn_5112_28"]
    assert hits[0].metadata["case_numbers"] == ["DKN.5112.28.2022"]
    hits = conn.search(_client(calls), "sklep internetowy", limit=10, filters={"date_from": "2023-01-01"})
    assert all(h.metadata["judgment_date"] >= "2023-01-01" for h in hits) and len(hits) == 5


def test_search_skips_foreign_case_numbers_and_court_types_without_requests():
    calls: list = []
    conn = UodoConnector()
    assert conn.search(_client(calls), "x", filters={"case_number": "KIO 1550/25"}) == []
    assert conn.search(_client(calls), "x", filters={"court_type": "NATIONAL_APPEAL_CHAMBER"}) == []
    assert calls == []


def test_fetch_stores_decision_with_snapshot(tmp_path):
    store, calls = Store(tmp_path), []
    conn = UodoConnector()
    assert conn.fetch(store, _client(calls), "uodo:2022:dkn_5112_28") == "uodo:2022:dkn_5112_28"
    assert [c[0] for c in calls] == [f"/document/{URN}/content.data"]

    j = store.get_judgment("uodo:2022:dkn_5112_28")
    assert j.court_name == "Prezes UODO" and j.case_numbers == ["DKN.5112.28.2022"]
    assert str(j.judgment_date) == "2025-07-21" and j.finality == "final" and j.judgment_type == "ADMINISTRATIVE_DECISION"
    assert j.original_url == page_url(URN) and j.source_judgment_id == URN and not j.data_quality_flags
    assert [x.document_id for x in store.find_judgments_by_case_number("dkn.5112.28.2022")] == ["uodo:2022:dkn_5112_28"]

    doc = store.get_document("uodo:2022:dkn_5112_28")
    assert doc.kind == SourceKind.decision and doc.snapshot_id == j.snapshot_id
    assert doc.title == "Decyzja – Prezes UODO – DKN.5112.28.2022 – 2025-07-21"
    assert doc.metadata["final_since"] == "2025-08-25" and doc.metadata["publication_date"] == "2025-09-09"
    assert store.read_snapshot_bytes(j.snapshot_id) == (FX / "content_2022_dkn_5112_28.json").read_bytes()
    assert store.find_snapshot_by_url(content_data_url(URN)).snapshot_id == j.snapshot_id

    n = len(calls)
    assert conn.fetch(store, _client(calls), "uodo:2022:dkn_5112_28") == "uodo:2022:dkn_5112_28"
    assert len(calls) == n  # checkpoint: the stored snapshot is re-parsed, not re-fetched
    assert conn.fetch(store, _client(calls), "uodo:../../etc") is None and len(calls) == n


def test_multi_signature_decision_is_not_final_and_links_court(tmp_path):
    store = Store(tmp_path)
    UodoConnector().fetch(store, _client([]), "uodo:2019:zspr_405_67")
    j = store.get_judgment("uodo:2019:zspr_405_67")
    assert j.case_numbers == ["ZSPR.421.2.2019", "ZSPR.405.67.2019"] and j.finality == "not_final"
    assert store.get_document(j.document_id).metadata["court_refs"] == ["urn:ndoc:court:pl:sa:2024:ii_sa-wa_430"]


def test_unknown_urn_and_http_404_are_not_stored(tmp_path):
    store, calls = Store(tmp_path), []
    with pytest.raises(NotFoundUpstream):
        UodoConnector().fetch(store, _client(calls), "uodo:2099:xyz_0000_0")  # 200 + empty document upstream

    def handler(req):
        return httpx.Response(404, text="Not Found")

    c404 = PoliteClient(transport=httpx.MockTransport(handler), allowlist={HOST}, min_delay=0)
    with pytest.raises(NotFoundUpstream):
        UodoConnector().fetch(store, c404, "uodo:2022:dkn_5112_28")
    assert store.stats()["judgments"] == 0 and store.stats()["snapshots"] == 0


def test_sync_bulk_checkpoint_and_resume(tmp_path):
    store, calls = Store(tmp_path), []
    conn = UodoConnector()
    params = {"since": "2025-04-01", "until": "2025-09-30"}

    r1 = conn.sync_bulk(store, _client(calls), params, BulkLimits(limit=1))
    assert r1.ok and r1.counts["stored"] == 1 and store.get_judgment("uodo:2022:dkn_5112_28") is not None
    assert calls[0] == ("/search.data", {"dcr": ["rodo"], "dtps": ["2025-04-01"], "dtpe": ["2025-09-30"], "page": ["1"]})
    assert any("2025:dkn_5131_11" in w and "not stored" in w for w in r1.warnings)
    assert store.get_judgment("uodo:2025:dkn_5131_11") is None
    state = store.list_sync_state("uodo")[0]
    assert state["cursor"] == "1" and not state["done"]
    # listing metadata (subject, keywords) is kept with the stored decision
    listing = store.get_document("uodo:2022:dkn_5112_28").metadata["listing"]
    assert listing["subject"].startswith("udzielenie upomnienia") and listing["keywords"]

    calls.clear()
    r2 = conn.sync_bulk(store, _client(calls), params, BulkLimits())
    assert r2.ok and r2.counts["stored"] == 1 and store.get_judgment("uodo:2022:dkn_5110_14") is not None
    assert f"/document/{URN}/content.data" not in [c[0] for c in calls]
    state = store.list_sync_state("uodo")[0]
    assert state["done"] and state["cursor"] == "3" and state["items"] == 13

    r3 = conn.sync_bulk(store, _client(calls), params, BulkLimits())
    assert r3.ok and any("already complete" in w for w in r3.warnings)


def test_sync_offline_with_listing_metadata(tmp_path):
    store = Store(tmp_path)
    res = UodoConnector().sync_offline(store, FX.parent)
    assert res.ok and res.counts == {"decisions": 3}, res.errors
    assert store.get_document("uodo:2022:dkn_5110_14").metadata["listing"]["subject"]
    assert store.get_source("uodo").access_status == "degraded"
    assert store.fts_search('"rejestru czynności przetwarzania"', ["judgment"], 10, 0, ["uodo"])


def test_turbo_stream_decoding_and_empty_placeholder():
    d = decode_turbo_stream((FX / "content_404.json").read_bytes())
    route = d["routes/_main.document.($urn).content"]["data"]
    assert route["status"] == "unknown" and route["refname"] == "" and route["dates"] == []
    assert d["root"]["data"]["plausibleHost"] == "https://orzeczenia.uodo.gov.pl"
    with pytest.raises(ValueError):
        decode_turbo_stream(b"<html>")


def test_html_to_text_keeps_paragraphs():
    body = parse_content((FX / "content_2022_dkn_5112_28.json").read_bytes())["body"]
    lines = body_to_text(body).split("\n")
    assert lines[0] == "Decyzja DKN.5112.28.2022"
    assert any(ln.startswith("I. Stwierdzając naruszenie przez G. R.") for ln in lines)
    assert any(ln.startswith("a) art. 29 i art. 32 ust. 4 rozporządzenia 2016/679") for ln in lines)
    assert "II. W pozostałym zakresie umarza postępowanie." in lines and "Uzasadnienie" in lines
    assert all("<" not in ln for ln in lines) and "" not in lines
