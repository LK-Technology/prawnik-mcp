"""Cellar: OJ text plus the latest consolidated version stored as a separate version (mocked transport)."""

from pathlib import Path

import httpx

from prawnik_mcp.connectors.cellar import consolidated_versions, sync_celex
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.store import Store

FX = Path(__file__).parent / "fixtures" / "raw"
XHTML = (FX / "celex_32011L0083.xhtml").read_bytes()
SPARQL = {"results": {"bindings": [{"c": {"value": v}} for v in
                                   ("02011L0083-20180701", "02011L0083-20260927", "02011L0083-20111122")]}}


def _client(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "/webapi/rdf/sparql" in str(req.url):
            return httpx.Response(200, json=SPARQL)
        return httpx.Response(200, content=XHTML, headers={"content-type": "application/xhtml+xml"})

    return PoliteClient(transport=httpx.MockTransport(handler), min_delay=0, sleep=lambda s: None)


def test_consolidated_versions_sorted_newest_first():
    assert consolidated_versions(_client([]), "32011L0083")[0] == "02011L0083-20260927"


def test_sync_stores_oj_and_consolidated_versions(tmp_path):
    store, calls = Store(tmp_path), []
    res = sync_celex(store, _client(calls), "32011L0083")
    assert res.version_id == "celex:32011L0083:consolidated:2026-09-27"
    versions = set(store.list_versions("celex:32011L0083"))
    assert versions == {"celex:32011L0083:oj", "celex:32011L0083:consolidated:2026-09-27"}
    doc = store.get_document("celex:32011L0083")
    assert doc.metadata["consolidated"] and doc.metadata["consolidated_versions_available"][0].endswith("20260927")
    cons = store.get_provisions("celex:32011L0083", "art. 9", "celex:32011L0083:consolidated:2026-09-27")[0]
    assert cons.temporal_basis.value == "consolidated_text" and "dokumentacyjny" in cons.version_label
    assert any("02011L0083-20260927" in c for c in calls)
