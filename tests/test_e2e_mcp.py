"""End-to-end through the MCP protocol (in-process client) on the offline corpus built from real fixtures.

This checks the technical path only: search -> exact text -> claims -> check_citations -> render.
It does not evaluate legal correctness (no model, no lawyer).
"""

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest
from mcp import Client

from prawnik_mcp.mcp_server.server import build_server
from prawnik_mcp.store import Store
from prawnik_mcp.sync import sync_corpus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from generate_examples import EXAMPLES  # noqa: E402  fictional parties, reused as inputs

# Source each template's letter relies on (quote taken from the corpus at test time, never typed in).
SOURCES = {
    "wezwanie_do_zaplaty": ("eli:DU/1964/93", "art. 481 § 1"),
    "reklamacja_konsumencka": ("eli:DU/2014/827", "art. 43a"),
    "odstapienie_od_umowy_na_odleglosc": ("eli:DU/2014/827", "art. 27"),
}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    st = Store(tmp_path_factory.mktemp("corpus"))
    sync_corpus(st, offline_fixtures=ROOT / "tests" / "fixtures" / "raw")
    return st


def _payload(result) -> dict:
    sc = getattr(result, "structured_content", None)
    if sc:
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


async def _call(c, name, args):
    return _payload(await c.call_tool(name, args))


async def _flow(store, template_id, tamper=None):
    ex = EXAMPLES[template_id]
    facts, draft = copy.deepcopy(ex["facts"]), ex["draft"] or None
    doc_id, loc = SOURCES[template_id]
    async with Client(build_server(store)) as c:
        got = await _call(c, "get_legal_document", {"document_id": doc_id, "locator": loc})
        assert got["status"] in ("ok", "temporal_unknown"), got["warnings"]
        quote = got["data"]["text"][:160]
        if tamper == "quote":
            quote = quote.replace("konsument", "przedsiębiorca").replace("dłużnik", "wierzyciel") + " (dopisek)"
        claims = [
            {"claim_id": "c1", "text": "Podstawa prawna żądania.", "type": "law", "evidence_ids": ["e1"]},
            {"claim_id": "f1", "text": "Fakty wskazane przez użytkownika.", "type": "fact"},
        ]
        evidence = [{"evidence_id": "e1", "document_id": doc_id, "locator": loc, "quote": quote}]
        rep = await _call(c, "check_citations", {
            "claims": claims, "evidence": evidence,
            "binding": {"template_id": template_id, "facts": facts, "draft": draft}})
        assert rep["status"] == "ok"
        report_id = rep["data"]["report_id"]
        if tamper == "facts":
            facts["data_pisma"] = "2026-09-25"
        out = await _call(c, "render_document", {
            "template_id": template_id, "facts": facts, "draft": draft, "report_id": report_id})
        return rep["data"], out


@pytest.mark.parametrize("template_id", list(SOURCES))
def test_full_path_renders_filled_document(store, template_id):
    report, out = asyncio.run(_flow(store, template_id))
    assert report["claims"][0]["citation_status"] in ("verified_exact", "verified_normalized")
    assert report["claims"][0]["semantic_review_status"] == "not_performed"
    assert out["status"] == "ok", out["data"].get("reasons")
    paths = json.dumps(out["data"], ensure_ascii=False)
    assert ".docx" in paths and ".md" in paths


def test_changed_facts_invalidate_report(store):
    _, out = asyncio.run(_flow(store, "wezwanie_do_zaplaty", tamper="facts"))
    assert out["status"] == "blocked"


def test_unfaithful_quote_blocks_export(store):
    report, out = asyncio.run(_flow(store, "odstapienie_od_umowy_na_odleglosc", tamper="quote"))
    assert report["critical_errors"]
    assert out["status"] == "blocked"
