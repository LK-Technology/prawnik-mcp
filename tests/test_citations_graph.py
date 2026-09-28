"""Citation graph (judgment -> statute/judgment) and act version timeline, on the offline corpus."""

from pathlib import Path

import pytest

from prawnik_mcp import service
from prawnik_mcp.citation_graph import edges_for
from prawnik_mcp.contracts import LegalDocument, SourceKind
from prawnik_mcp.store import Store
from prawnik_mcp.sync import sync_corpus

FX = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    st = Store(tmp_path_factory.mktemp("corpus"))
    sync_corpus(st, offline_fixtures=FX)
    return st


def test_edges_from_text_only_metadata():
    doc = LegalDocument(document_id="saos:1", kind=SourceKind.judgment, title="t", original_url="https://x",
                        snapshot_id="s", sha256="h", metadata={"referenced_regulations": [
                            "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny (Dz. U. z 1964 r. Nr 16, poz. 93 - art. 448; art. 24 § 1)"]})
    edges = edges_for(doc)
    assert {(e.target, e.locator) for e in edges} == {("eli:DU/1964/93", "art. 448"), ("eli:DU/1964/93", "art. 24 § 1")}


def test_outgoing_marks_corpus_membership(store):
    r = service.get_citations(store, "saos:31345", direction="outgoing")
    by_target = {o["target"]: o for o in r.data["outgoing"]}
    assert by_target["eli:DU/1964/93"]["status"] == "in_corpus"
    assert "art. 448" in by_target["eli:DU/1964/93"]["locators"]
    assert by_target["eli:DU/1964/296"]["status"] == "out_of_corpus"  # KPC not synced
    assert any(o["kind"] == "court_case" for o in r.data["outgoing"])


def test_incoming_by_article(store):
    r = service.get_citations(store, "eli:DU/1964/93", direction="incoming", locator="art. 448")
    assert r.data["incoming_total"] >= 1
    assert any(i["document_id"] == "saos:31345" for i in r.data["incoming"])
    assert any("tylko lokalny korpus" in w for w in r.warnings)


def test_act_versions_timeline(store):
    r = service.list_act_versions(store, "eli:DU/1964/93", live=False)
    assert r.status.value == "ok"
    assert r.data["consolidated_texts"][0] == {"eli": "DU/2026/795", "parsed_locally": True}
    assert any(a["id"] == "DU/2026/507" and a["pending"] for a in r.data["pending_amendments"])
    assert service.list_act_versions(store, "celex:32011L0083").status.value == "out_of_scope"


def test_saos_reference_without_comma_and_superscript_articles():
    doc = LegalDocument(document_id="saos:2", kind=SourceKind.judgment, title="t", original_url="https://x",
                        snapshot_id="s", sha256="h", metadata={"referenced_regulations_struct": [{
                            "year": 2013, "entry": 1222,
                            "text": "Obwieszczenie (Dz. U. z 2013 r. Nr 0 poz. 1222 - art. 4 ust. 11, art. 171(1) ust. 1)"}]})
    assert {e.locator for e in edges_for(doc)} == {"art. 4 ust. 11", "art. 171^1 ust. 1"}


def test_incoming_includes_citations_of_consolidated_text_notices(store):
    doc = LegalDocument(document_id="saos:3", kind=SourceKind.judgment, title="t", original_url="https://x",
                        snapshot_id="s", sha256="h", metadata={"referenced_regulations_struct": [{
                            "year": 2026, "entry": 795, "text": "Obwieszczenie (Dz. U. z 2026 r. poz. 795 - art. 471)"}]})
    store.upsert_document(doc)
    r = service.get_citations(store, "eli:DU/1964/93", direction="incoming", locator="art. 471")
    assert any(i["document_id"] == "saos:3" for i in r.data["incoming"])
    out = service.get_citations(store, "saos:3", direction="outgoing").data["outgoing"][0]
    assert out["status"] == "in_corpus" and out["resolved_act"] == "eli:DU/1964/93"
    store.delete_document("saos:3")
