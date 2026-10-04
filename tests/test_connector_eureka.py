"""EUREKA connector — offline, httpx.MockTransport serving responses recorded live on 2026-09-28."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from prawnik_mcp.connectors.base import BulkLimits
from prawnik_mcp.connectors.eureka import EurekaConnector
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient
from prawnik_mcp.parsers.eureka import EurekaDataError, parse_eureka_detail, parse_issue_date
from prawnik_mcp.store import Store

FX_ROOT = Path(__file__).parent / "fixtures" / "raw"
FX = FX_ROOT / "eureka"
HOST = "eureka.mf.gov.pl"
SIG = "0115-KDIT3.4011.582.2026.2.AWO"
FETCHED = datetime(2026, 9, 28, tzinfo=UTC)
BULK = {"query": "ulga termomodernizacyjna", "since": "2026-08-22", "until": "2026-08-29", "page_size": 2}


def _handler(calls, pages=None, search_file="wyszukiwarka_termomodernizacja_size3_p0.json"):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "POST":
            body = json.loads(req.content)
            if "SYG" in body["filter"]:
                name = "wyszukiwarka_syg_0115-KDIT3.json"
            elif pages is not None:
                name = pages[int(req.url.params["page"])]
            else:
                name = search_file
            return httpx.Response(200, content=(FX / name).read_bytes(), headers={"content-type": "application/json"})
        eid = req.url.path.rsplit("/", 1)[-1]
        p = FX / f"informacje_{eid}.json"
        if p.exists():
            return httpx.Response(200, content=p.read_bytes(), headers={"content-type": "application/json"})
        return httpx.Response(404, content=(FX / "informacje_99999999.404.json").read_bytes())
    return handler


def _client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), allowlist={HOST}, min_delay=0, sleep=lambda s: None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    yield s
    s.close()


def test_search_maps_hits_and_request_shape():
    calls = []
    hits = EurekaConnector().search(_client(_handler(calls)), "ulga termomodernizacyjna", limit=3,
                                    filters={"date_from": "2026-08-01", "date_to": "2026-08-31"})
    req = calls[0]
    assert req.method == "POST" and req.url.path == "/api/public/v1/wyszukiwarka/informacje/"
    assert req.url.params["size"] == "3" and req.url.params["page"] == "0"
    assert req.url.params.get_list("sort") == ["DT_WYD,desc", "ID_INFORMACJI,desc"]
    body = json.loads(req.content)
    assert body["searchQuery"] == "ulga termomodernizacyjna" and body["searchInFullPhrase"] is True
    assert body["filter"] == {"KATEGORIA_INFORMACJI": [1, 3, 4, 11], "DT_WYD_start": "2026-08-01", "DT_WYD_end": "2026-08-31"}
    # results come newest first; theses about selling electricity do not mention the relief and are dropped
    assert [h.document_id for h in hits] == ["eureka:706823"]
    h = hits[0]
    assert h.kind == "tax_ruling" and SIG in h.title and "2026-08-24" in h.title
    assert h.snippet.startswith("Brak możliwości skorzystania z ulgi termomodernizacyjnej")
    assert h.original_url == "https://eureka.mf.gov.pl/informacje/podglad/706823"
    assert h.metadata["total_hits"] == 9 and h.metadata["status"] == "Aktualna" and h.metadata["signature"] == SIG


def test_search_by_signature_uses_syg_filter_without_query():
    calls = []
    hits = EurekaConnector().search(_client(_handler(calls)), SIG)
    body = json.loads(calls[0].content)
    assert body["filter"]["SYG"] == SIG and "searchQuery" not in body
    assert [h.document_id for h in hits] == ["eureka:706823"]


def test_fetch_stores_document_with_snapshot_and_reuses_it(store):
    calls = []
    client = _client(_handler(calls))
    assert EurekaConnector().fetch(store, client, "eureka:706823") == "eureka:706823"
    doc = store.get_document("eureka:706823")
    assert doc.kind.value == "tax_ruling" and doc.metadata["signature"] == SIG
    assert doc.metadata["status"] == "Aktualna" and doc.metadata["author_ids"] == ["70"]
    j = store.get_judgment("eureka:706823")
    assert j.court_name == "Dyrektor Krajowej Informacji Skarbowej" and j.case_numbers == [SIG]
    assert j.judgment_date == date(2026, 8, 24) and j.judgment_type == "Interpretacja indywidualna"
    assert "pompy ciepła typu powietrze-powietrze" in j.text and "<span" not in j.text
    assert j.data_quality_flags == []
    snap = store.get_snapshot(doc.snapshot_id)
    assert snap.url == "https://eureka.mf.gov.pl/api/public/v1/informacje/706823"
    assert store.read_snapshot_bytes(snap.snapshot_id) == (FX / "informacje_706823.json").read_bytes()
    assert [x.document_id for x in store.find_judgments_by_case_number(SIG)] == ["eureka:706823"]
    assert any(r[2] == "eureka:706823" for r in store.fts_search("termomodernizacyjnej", None, 5, 0))
    n = len(calls)
    EurekaConnector().fetch(store, client, "eureka:706823")  # checkpoint: snapshot re-parsed, no request
    assert len(calls) == n


def test_fetch_404_is_not_stored(store):
    conn, calls = EurekaConnector(), []
    with pytest.raises(NotFoundUpstream):
        conn.fetch(store, _client(_handler(calls)), "eureka:99999999")
    assert store.get_document("eureka:99999999") is None and store.stats()["snapshots"] == 0
    assert conn.fetch(store, _client(_handler(calls)), "saos:1") is None
    with pytest.raises(EurekaDataError):  # the recorded 404 error body is not a document
        parse_eureka_detail((FX / "informacje_99999999.404.json").read_bytes(), "s", "h", FETCHED)


def test_sync_bulk_checkpoint_and_resume(store):
    conn, calls = EurekaConnector(), []
    pages = {0: "wyszukiwarka_bulk_size2_p0.json", 1: "wyszukiwarka_bulk_size2_p1.json"}
    r1 = conn.sync_bulk(store, _client(_handler(calls, pages)), BULK, BulkLimits(limit=1))
    assert r1.ok and r1.counts["stored"] == 1 and store.stats()["documents"] == 1
    state = store.list_sync_state("eureka")[0]
    assert state["cursor"] == "0" and not state["done"]
    r2 = conn.sync_bulk(store, _client(_handler(calls, pages)), BULK, BulkLimits())
    assert r2.ok and r2.counts["stored"] == 2 and r2.counts["total_hits"] == 3
    assert {d.document_id for d in store.list_documents()} == {"eureka:706781", "eureka:706823", "eureka:705888"}
    state = store.list_sync_state("eureka")[0]
    assert state["done"] and state["cursor"] == "2"
    gets = [str(c.url) for c in calls if c.method == "GET"]
    assert len(gets) == len(set(gets)) == 3  # every document fetched exactly once across runs
    r3 = conn.sync_bulk(store, _client(_handler(calls, pages)), BULK, BulkLimits())
    assert r3.ok and any("already complete" in w for w in r3.warnings)


def test_sync_bulk_warns_on_unstable_pagination(store):
    pages = {0: "wyszukiwarka_bulk_size2_p0.json", 1: "wyszukiwarka_bulk_size2_p1_unstable.json"}
    r = EurekaConnector().sync_bulk(store, _client(_handler([], pages)), BULK, BulkLimits())
    assert r.counts["stored"] == 2 and store.stats()["documents"] == 2  # duplicate id not stored twice
    assert any("repeated 1 id" in w for w in r.warnings)
    assert any("totalHits=3" in w for w in r.warnings)


def test_sync_offline(store):
    conn = EurekaConnector()
    r = conn.sync_offline(store, FX_ROOT)
    assert r.ok and r.counts == {"documents": 3} and not r.errors
    assert store.stats_by_source()["eureka"] == {"documents": 3, "judgments": 3}
    assert store.get_source("eureka").access_status == "degraded"  # offline samples, not a sync
    assert conn.coverage(store)[0].startswith("3 EUREKA")


def test_issue_date_flags():
    today = date(2026, 9, 28)
    assert parse_issue_date("2026-08-24T19:59:16.989Z", today) == (date(2026, 8, 24), [])
    d, flags = parse_issue_date("2026-08-23T22:30:00Z", today)  # after midnight in Warsaw
    assert d == date(2026, 8, 24) and flags[0].startswith("issue_date_local_differs_from_utc")
    assert parse_issue_date("3013-01-01", today)[0] is None
    assert parse_issue_date(None, today) == (None, ["issue_date_missing"])
