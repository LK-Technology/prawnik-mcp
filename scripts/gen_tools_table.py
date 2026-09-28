#!/usr/bin/env python3
"""Render the MCP tools table from the server definition into README.md / README.pl.md.

Replaces the block between <!-- tools:start --> and <!-- tools:end -->. Use --check in CI.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path

from mcp import Client

from prawnik_mcp.mcp_server.server import build_server
from prawnik_mcp.store import Store

ROOT = Path(__file__).resolve().parents[1]


async def _tools() -> list:
    with tempfile.TemporaryDirectory() as d:
        async with Client(build_server(Store(d))) as c:
            return (await c.list_tools()).tools


HEADER = {"README.md": "| Tool | What it does | Main arguments |",
          "README.pl.md": "| Narzędzie | Co robi (opis widoczny dla modelu) | Główne argumenty |"}


def table(tools: list, readme: str = "README.md") -> str:
    rows = [HEADER[readme], "|---|---|---|"]
    for t in tools:
        first = re.split(r"(?<=[.!?])\s", (t.description or "").strip(), maxsplit=1)[0].replace("|", "\\|")
        props = list((t.input_schema or {}).get("properties", {}))
        rows.append(f"| `{t.name}` | {first} | {', '.join(f'`{p}`' for p in props) or '—'} |")
    return "\n".join(rows)


def main() -> int:
    check = "--check" in sys.argv
    tools, stale = asyncio.run(_tools()), False
    for name in ("README.md", "README.pl.md"):
        p = ROOT / name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        start, end = "<!-- tools:start -->", "<!-- tools:end -->"
        if start not in text:
            continue
        head, rest = text.split(start, 1)
        new = f"{head}{start}\n{table(tools, name)}\n{end}{rest.split(end, 1)[1]}"
        if new != text:
            stale = True
            if not check:
                p.write_text(new, encoding="utf-8")
    if check and stale:
        print("README tools table is out of date: run python scripts/gen_tools_table.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
