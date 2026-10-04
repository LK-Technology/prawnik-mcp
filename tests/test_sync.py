"""Offline end-to-end sync into a temporary Store; optional online smoke test."""

import os
from datetime import date
from pathlib import Path

import pytest

from prawnik_mcp.store import Store
from prawnik_mcp.sync import sync_corpus

FX = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture(scope="module")
def synced(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("store"))
    report = sync_corpus(store, offline_fixtures=FX)
    yield store, report
    store.close()


def test_offline_counts(synced):
    store, report = synced
    assert report.ok and report.mode == "offline_fixtures"
    assert report.sources["eli"].counts == {"DU/2014/827": 83, "DU/1964/93": 1296}
    assert report.sources["cellar"].counts == {"32011L0083": 35}
    assert report.sources["saos"].counts["judgments"] >= 9
    assert store.stats()["provisions"] == 1414


def test_provision_versions(synced):
    store, _ = synced
    p = store.get_provisions("eli:DU/2014/827", "art. 27")[0]
    assert p.version_id == "eli:DU/2026/1244" and p.text_state_date == date(2026, 9, 2)
    assert p.temporal_basis.value == "consolidated_text" and p.valid_from is None and p.valid_to is None
    kc = store.get_provisions("eli:DU/1964/93", "art. 471")[0]
    assert any("2026 poz. 507" in x and "2028-11-01" in x for x in kc.pending_changes)
    assert any("Art. 13. Do umów zawartych" in x for x in kc.excluded_provisions)
    doc = store.get_document("eli:DU/1964/93")
    assert {"id": "DU/2026/507", "date": "2028-11-01"} in doc.metadata["amendments"]
    assert doc.metadata["consolidated_versions"][0]["state_date"] == "2026-05-19"
    eu = store.get_provisions("celex:32011L0083", "art. 9")[0]
    assert eu.version_id == "celex:32011L0083:oj" and eu.temporal_basis.value == "original_publication"


def test_judgments_and_sources(synced):
    store, _ = synced
    assert store.get_judgment("saos:31345").judgment_date is None
    srcs = {s.source_id: s for s in store.get_sources()}
    assert {"eli", "saos", "cellar"} <= set(srcs)
    assert all(s.terms_checked_at >= date(2026, 9, 26) for s in srcs.values())
    assert all(s.last_successful_sync is None for s in srcs.values())  # offline fixtures are not a sync


def test_fts_finds_upk_art_27(synced):
    store, _ = synced
    hits = store.fts_search('odstąpić AND "14 dni"', ["provision"], 5, 0)
    assert hits[0][0] == "eli:DU/2014/827#art. 27@eli:DU/2026/1244"


def test_resync_idempotent(synced):
    store, report = synced
    again = sync_corpus(store, offline_fixtures=FX)
    assert again.store_stats == report.store_stats


@pytest.mark.online
@pytest.mark.skipif(os.environ.get("PRAWNIK_ONLINE") != "1", reason="set PRAWNIK_ONLINE=1 to hit public APIs")
def test_online_smoke(tmp_path):
    store = Store(tmp_path)
    report = sync_corpus(store, saos_max=3)
    assert report.sources["eli"].ok, report.sources["eli"].errors
    assert store.get_provisions("eli:DU/2014/827", "art. 27")
    assert report.sources["cellar"].ok, report.sources["cellar"].errors
    assert report.store_stats["judgments"] >= 1
