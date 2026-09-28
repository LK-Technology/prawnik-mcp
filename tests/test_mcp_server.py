import asyncio
import json

from mcp import Client

from prawnik_mcp.mcp_server.server import build_server
from prawnik_mcp.store import Store

TOOLS = {"search_legal", "get_legal_document", "check_citations", "get_document_template",
         "render_document", "sources_status"}


def _payload(result) -> dict:
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


def test_server_lists_six_tools_and_handles_empty_corpus(tmp_path):
    server = build_server(Store(tmp_path))

    async def run():
        async with Client(server) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert TOOLS <= tools
            r = _payload(await c.call_tool("search_legal", {"query": "odstąpienie od umowy"}))
            assert r["status"] == "source_unavailable"
            assert any("pusty" in w for w in r["warnings"])
            r = _payload(await c.call_tool("get_legal_document", {"document_id": "eli:DU/2014/827", "locator": "art. 27"}))
            assert r["status"] == "not_found"
            prompts = {p.name for p in (await c.list_prompts()).prompts}
            assert "analysis_procedure" in prompts

    asyncio.run(run())
