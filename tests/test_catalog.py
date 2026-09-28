"""Source catalog and connector registry."""

from prawnik_mcp import sources
from prawnik_mcp.connectors import registry


def test_catalog_entries_are_complete():
    for s in sources.catalog().values():
        assert s.hosts and s.id_prefix.endswith(":") and s.terms and s.attribution
        assert s.maturity in {"stable", "beta", "experimental", "research"}
        assert 0 < s.rate_per_s <= 2


def test_every_implemented_source_has_a_connector_and_vice_versa():
    implemented = {s.source_id for s in sources.implemented_sources()}
    assert {c.source_id for c in registry.all_connectors()} == implemented


def test_aliases_and_document_attribution():
    assert sources.act_aliases()["upk"] == "eli:DU/2014/827"
    assert sources.source_for_document("saos:31345") == "saos"
    assert registry.for_document("celex:32011L0083").source_id == "cellar"
    assert registry.for_document("unknown:1") is None
