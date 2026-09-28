"""CBOSA connector — offline, httpx.MockTransport over real pages recorded from orzeczenia.nsa.gov.pl."""

import dataclasses
import time
from pathlib import Path
from urllib.robotparser import RobotFileParser

import httpx
import pytest

from prawnik_mcp import sources
from prawnik_mcp.connectors.base import BulkLimits
from prawnik_mcp.connectors.cbosa import HOST, ROBOTS_NOTE, CbosaConnector
from prawnik_mcp.connectors.http import PoliteClient, SourceUnavailable
from prawnik_mcp.store import Store

FX = Path(__file__).parent / "fixtures" / "raw" / "cbosa"
DOCS = {h: (FX / f"doc_{h}.html").read_bytes() for h in ("9FF3766DA4", "3BB1F6C423", "4DC5BD4680")}
BASE = "https://orzeczenia.nsa.gov.pl"


class Site:
    """Fake CBOSA serving the recorded /doc/ pages; records every request."""

    def __init__(self, status: int = 200, body: bytes | None = None):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        doc = req.url.path.rsplit("/", 1)[-1]
        if not req.url.path.startswith("/doc/") or (self.body is None and doc not in DOCS):
            return httpx.Response(404)
        return httpx.Response(self.status, headers={"content-type": "text/html; charset=UTF-8"},
                              content=self.body if self.body is not None else DOCS[doc])


def _client(site: Site) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(site), allowlist={HOST}, min_delay=0, sleep=lambda s: None)


def _conn(**kw) -> CbosaConnector:
    return CbosaConnector(min_interval_s=0, **kw)


def test_recorded_robots_txt_disallows_search_and_allows_doc():
    rp = RobotFileParser()
    rp.parse((FX / "robots.txt").read_text(encoding="utf-8").splitlines())
    ua = "prawnik-mcp"
    assert not rp.can_fetch(ua, f"{BASE}/cbo/search") and not rp.can_fetch(ua, f"{BASE}/cbo/find?p=2")
    assert rp.can_fetch(ua, f"{BASE}/doc/9FF3766DA4")


def test_search_and_sync_bulk_make_zero_requests(tmp_path):
    site, conn = Site(), _conn()
    client = _client(site)
    assert conn.supports_search is False and conn.supports_fetch is True
    with pytest.raises(NotImplementedError, match="robots.txt"):
        conn.search(client, "RODO", limit=5, filters={"case_number": "II SA/Wa 1553/24"})
    res = conn.sync_bulk(Store(tmp_path), client, {"query": "RODO", "since": "2025-03-01"}, BulkLimits())
    assert res.ok is False and res.warnings == [ROBOTS_NOTE]
    assert ROBOTS_NOTE == "CBOSA robots.txt disallows /cbo/search and /cbo/find; only single /doc/{hex} fetches are supported"
    assert site.calls == []


@pytest.mark.parametrize("doc_id, court, case, day, finality", [
    ("9FF3766DA4", "Naczelny Sąd Administracyjny", "III OSK 6859/21", "2025-03-27", "final"),
    ("3BB1F6C423", "Wojewódzki Sąd Administracyjny w Łodzi", "II SAB/Łd 23/25", "2025-03-26", "final"),
    ("4DC5BD4680", "Wojewódzki Sąd Administracyjny w Warszawie", "II SA/Wa 1553/24", "2025-03-27", "not_final"),
])
def test_fetch_parses_metadata(tmp_path, doc_id, court, case, day, finality):
    store = Store(tmp_path)
    assert _conn().fetch(store, _client(Site()), f"cbosa:{doc_id}") == f"cbosa:{doc_id}"
    j = store.get_judgment(f"cbosa:{doc_id}")
    assert (j.court_name, j.case_numbers, str(j.judgment_date), j.finality) == (court, [case], day, finality)
    assert j.court_type == "ADMINISTRATIVE" and j.judgment_type == "SENTENCE" and not j.data_quality_flags
    assert j.original_url == f"{BASE}/doc/{doc_id}"
    assert j.text.startswith("Sentencja\n") and "\n\nUzasadnienie\n" in j.text
    doc = store.get_document(f"cbosa:{doc_id}")
    assert doc.kind.value == "judgment" and doc.metadata["finality_as_of"] and "zbioru urzędowego" in doc.metadata["source_note"]
    assert store.find_judgments_by_case_number(case.lower())[0].document_id == f"cbosa:{doc_id}"


def test_fetch_stores_snapshot_and_reuses_it(tmp_path):
    store, site, conn = Store(tmp_path), Site(), _conn()
    client = _client(site)
    conn.fetch(store, client, "cbosa:9ff3766da4")  # ids are case-insensitive
    snap = store.find_snapshot_by_url(f"{BASE}/doc/9FF3766DA4")
    assert snap and store.read_snapshot_bytes(snap.snapshot_id) == DOCS["9FF3766DA4"]
    j = store.get_judgment("cbosa:9FF3766DA4")
    assert j.snapshot_id == snap.snapshot_id
    meta = store.get_document("cbosa:9FF3766DA4").metadata
    assert meta["related"] == [{"document_id": "cbosa:5F69705170",
                                "label": "II SA/Wa 2489/19 - Wyrok WSA w Warszawie z 2021-07-08"}]
    assert store.fts_search("kasacyjną", ["judgment"], 5, 0, ["cbosa"])
    conn.fetch(store, client, "cbosa:9FF3766DA4")  # checkpoint: no second request
    assert site.calls == [("GET", "/doc/9FF3766DA4")]
    conn.fetch(store, client, "cbosa:9FF3766DA4", force=True)
    assert len(site.calls) == 2
    assert conn.fetch(store, client, "saos:123") is None and conn.fetch(store, client, "cbosa:XYZ") is None


def test_403_raises_and_stops_further_requests(tmp_path):
    site, conn = Site(status=403, body=b"Forbidden"), _conn()
    client, store = _client(site), Store(tmp_path)
    with pytest.raises(SourceUnavailable) as e:
        conn.fetch(store, client, "cbosa:9FF3766DA4")
    assert e.value.http_status == 403
    with pytest.raises(SourceUnavailable, match="403"):
        conn.fetch(store, _client(site), "cbosa:3BB1F6C423")  # even through a fresh client
    assert len(site.calls) == 1 and store.stats()["judgments"] == 0


def test_page_without_judgment_metadata_is_not_stored(tmp_path):
    site, store = Site(body=b"<html><body>Request rejected</body></html>"), Store(tmp_path)
    with pytest.raises(SourceUnavailable):
        _conn().fetch(store, _client(site), "cbosa:9FF3766DA4")
    assert store.stats()["judgments"] == 0 and store.stats()["snapshots"] == 0


def test_requests_are_spaced_across_clients(tmp_path):
    site, store, conn = Site(), Store(tmp_path), CbosaConnector(min_interval_s=0.2)
    t0 = time.monotonic()
    for doc_id in DOCS:  # a fresh PoliteClient each time, as live.py does
        conn.fetch(store, _client(site), f"cbosa:{doc_id}")
    assert time.monotonic() - t0 >= 0.4 and len(site.calls) == 3


def test_sync_offline(tmp_path):
    store = Store(tmp_path)
    res = _conn().sync_offline(store, FX.parent)
    assert res.ok and res.counts == {"judgments": 3}
    assert store.get_source("cbosa").access_status == "degraded"
    snap = store.get_snapshot(store.get_judgment("cbosa:4DC5BD4680").snapshot_id)
    assert snap.fetched_at.isoformat() == "2026-09-28T13:58:12+00:00"  # from manifest.json


def test_sync_defaults_resumes_from_snapshots_after_403(tmp_path, monkeypatch):
    info = dataclasses.replace(sources.source("cbosa"), defaults={"doc_ids": list(DOCS)})
    real = sources.source
    monkeypatch.setattr(sources, "source", lambda sid: info if sid == "cbosa" else real(sid))
    store, ok_site = Store(tmp_path), Site()

    served = []

    def flaky(req: httpx.Request) -> httpx.Response:  # first judgment served, then a 403
        served.append(req.url.path)
        return ok_site(req) if len(served) == 1 else httpx.Response(403)

    r1 = _conn().sync_defaults(store, PoliteClient(transport=httpx.MockTransport(flaky), allowlist={HOST},
                                                   min_delay=0, sleep=lambda s: None))
    assert not r1.ok and r1.counts == {"fetched": 1, "reused_checkpoint": 0} and "403" in r1.errors[0]
    ok_site.calls.clear()
    r2 = _conn().sync_defaults(store, _client(ok_site))
    assert r2.ok and r2.counts == {"fetched": 2, "reused_checkpoint": 1}
    assert [p for _, p in ok_site.calls] == ["/doc/3BB1F6C423", "/doc/4DC5BD4680"]
    assert store.stats()["judgments"] == 3
