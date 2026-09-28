#!/usr/bin/env python3
"""Render the MCP tools and prompts tables from the server definition into README.md / README.pl.md.

Replaces the blocks between <!-- tools:start -->/<!-- tools:end --> and <!-- prompts:start -->/<!-- prompts:end -->.
Use --check in CI.
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


async def _listing() -> tuple[list, list]:
    with tempfile.TemporaryDirectory() as d:
        async with Client(build_server(Store(d))) as c:
            return (await c.list_tools()).tools, (await c.list_prompts()).prompts


HEADER = {"README.md": "| Tool | What it does | Main arguments |",
          "README.pl.md": "| Narzędzie | Co robi (opis widoczny dla modelu) | Główne argumenty |"}
PROMPT_HEADER = {"README.md": "| Prompt | Title | What it does |", "README.pl.md": "| Prompt | Tytuł | Co robi |"}


def table(tools: list, readme: str = "README.md") -> str:
    rows = [HEADER[readme], "|---|---|---|"]
    for t in tools:
        first = re.split(r"(?<=[.!?])\s", (t.description or "").strip(), maxsplit=1)[0].replace("|", "\\|")
        props = list((t.input_schema or {}).get("properties", {}))
        rows.append(f"| `{t.name}` | {first} | {', '.join(f'`{p}`' for p in props) or '—'} |")
    return "\n".join(rows)


def prompt_table(prompts: list, readme: str = "README.md") -> str:
    rows = [PROMPT_HEADER[readme], "|---|---|---|"]
    for p in prompts:
        rows.append(f"| `{p.name}` | {p.title or '—'} | {(p.description or '').replace('|', '\\|')} |")
    return "\n".join(rows)


def _replace(text: str, start: str, end: str, block: str) -> str:
    if start not in text:
        return text
    head, rest = text.split(start, 1)
    return f"{head}{start}\n{block}\n{end}{rest.split(end, 1)[1]}"


def main() -> int:
    check = "--check" in sys.argv
    (tools, prompts), stale = asyncio.run(_listing()), False
    for name in ("README.md", "README.pl.md"):
        p = ROOT / name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        new = _replace(text, "<!-- tools:start -->", "<!-- tools:end -->", table(tools, name))
        new = _replace(new, "<!-- prompts:start -->", "<!-- prompts:end -->", prompt_table(prompts, name))
        if new != text:
            stale = True
            if not check:
                p.write_text(new, encoding="utf-8")
    if check and stale:
        print("README tools/prompts tables are out of date: run python scripts/gen_tools_table.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
