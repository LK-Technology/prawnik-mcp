"""CLI: `prawnik-mcp serve | sync [--offline] | status | search | get`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prawnik_mcp import service
from prawnik_mcp.store import Store

# Only present in a source checkout; wheel installs must pass --fixtures explicitly.
REPO_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "raw"


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prawnik-mcp")
    ap.add_argument("--data", help="data directory (default: $PRAWNIK_MCP_DATA or the per-user data dir)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="uruchom serwer MCP (stdio)")
    s = sub.add_parser("sync", help="pobierz mały korpus MVP")
    s.add_argument("--offline", action="store_true", help="build the corpus from recorded samples (no network)")
    s.add_argument("--fixtures", help="directory with recorded samples (default: tests/fixtures/raw in a source checkout)")
    sub.add_parser("status")
    q = sub.add_parser("search")
    q.add_argument("query")
    q.add_argument("--date")
    q.add_argument("--limit", type=int, default=5)
    g = sub.add_parser("get")
    g.add_argument("document_id")
    g.add_argument("locator", nargs="?")
    g.add_argument("--as-of")
    a = ap.parse_args(argv)
    cmd = a.cmd or "serve"
    store = Store(a.data) if a.data else Store()

    if cmd == "serve":
        from prawnik_mcp.mcp_server.server import build_server

        build_server(store).run("stdio")
        return 0
    if cmd == "sync":
        from prawnik_mcp.sync import sync_corpus

        fixtures = None
        if a.offline:
            fixtures = Path(a.fixtures) if a.fixtures else REPO_FIXTURES
            if not fixtures.is_dir():
                ap.error("--offline needs --fixtures PATH (recorded samples are not shipped in the wheel)")
        rep = sync_corpus(store, offline_fixtures=fixtures)
        _print(rep.model_dump(mode="json") if hasattr(rep, "model_dump") else rep)
        return 0
    if cmd == "status":
        _print(service.sources_status(store).model_dump(mode="json"))
    elif cmd == "search":
        _print(service.search_legal(store, a.query, relevant_date=a.date, limit=a.limit).model_dump(mode="json"))
    elif cmd == "get":
        _print(service.get_legal_document(store, a.document_id, a.locator, a.as_of).model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
