#!/usr/bin/env python3
"""Smoke test of an installed server over stdio: list tools and call sources_status.

Usage: python scripts/smoke_stdio.py [path-to-prawnik-mcp-executable]
"""

import asyncio
import json
import shutil
import sys
import tempfile

from mcp import Client, StdioServerParameters

EXPECTED = {"search_legal", "get_legal_document", "check_citations", "get_document_template",
            "render_document", "sources_status"}


async def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else shutil.which("prawnik-mcp")
    if not exe:
        print("prawnik-mcp executable not found", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as data:
        async with Client(StdioServerParameters(command=exe, args=["--data", data, "serve"])) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            missing = EXPECTED - tools
            status = json.loads((await c.call_tool("sources_status", {})).content[0].text)["status"]
            templates = json.loads((await c.call_tool("get_document_template", {"template_id": "list"})).content[0].text)
    print(f"tools={len(tools)} missing={sorted(missing)} sources_status={status} templates={len(templates['data']['templates'])}")
    return 1 if missing or status != "ok" or len(templates["data"]["templates"]) < 3 else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
