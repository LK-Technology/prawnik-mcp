"""Command line: `prawnik-mcp serve | sync | status | search | get`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prawnik_mcp import __version__, service
from prawnik_mcp.store import Store

# Only present in a source checkout; wheel installs must pass --fixtures explicitly.
REPO_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "raw"

EPILOG = """examples:
  prawnik-mcp sync                                   small default corpus (KC, UPK, directive, SAOS sample)
  prawnik-mcp sync --offline                         same corpus from recorded samples, no network
  prawnik-mcp sync --source saos --court-type COMMON --query "przedawnienie" --limit 500
  prawnik-mcp sync --source sn --query "przedawnienie" --since 2025-01-01 --limit 50
  prawnik-mcp sync --source saos --since 2026-01-01 --max-gb 2      bulk dump (all courts) for a date window
  prawnik-mcp sync --source eli --act DU/2018/1000 --act DU/1964/16
  prawnik-mcp sync --source eli --query "ochronie danych osobowych" --limit 5
  prawnik-mcp sync --source cellar --celex 32016R0679
  prawnik-mcp search "odstąpienie od umowy" --live
  prawnik-mcp get eli:DU/2014/827 "art. 27 ust. 1"
"""


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prawnik-mcp", description="MCP server and CLI for Polish and EU legal sources.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"prawnik-mcp {__version__}")
    ap.add_argument("--data", help="data directory (default: $PRAWNIK_MCP_DATA or the per-user data dir)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")

    s = sub.add_parser("sync", help="download sources into the local corpus")
    s.add_argument("--offline", action="store_true", help="build the default corpus from recorded samples (no network)")
    s.add_argument("--fixtures", help="directory with recorded samples (default: tests/fixtures/raw in a checkout)")
    s.add_argument("--source", help="bulk/targeted sync of one source: eli | cellar | saos | sn | eureka | kio | uodo | cbosa")
    s.add_argument("--query", help="scope query (saos full text, eli title words, cellar identifiers)")
    s.add_argument("--court-type",
                   help="saos: COMMON | SUPREME | ADMINISTRATIVE | CONSTITUTIONAL_TRIBUNAL | NATIONAL_APPEAL_CHAMBER")
    s.add_argument("--since", help="saos: earliest judgment date (YYYY-MM-DD)")
    s.add_argument("--until", help="saos: latest judgment date (YYYY-MM-DD)")
    s.add_argument("--act", action="append", default=[], help="eli: act id such as DU/2018/1000 (repeatable)")
    s.add_argument("--celex", action="append", default=[], help="cellar: CELEX number (repeatable)")
    s.add_argument("--limit", type=int, help="maximum number of new items in this run")
    s.add_argument("--max-gb", type=float, help="stop when the data directory reaches this size")
    s.add_argument("--no-resume", action="store_true", help="ignore checkpoints and re-fetch")

    sub.add_parser("status", help="show source status and local coverage")
    q = sub.add_parser("search", help="search the local corpus (and sources with --live)")
    q.add_argument("query")
    q.add_argument("--date", help="event date for the version check (YYYY-MM-DD)")
    q.add_argument("--limit", type=int, default=5)
    q.add_argument("--live", action="store_true", help="also query source APIs now")
    q.add_argument("--local", action="store_true", help="local corpus only")
    g = sub.add_parser("get", help="exact text of a provision or judgment")
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
        return _sync(ap, a, store)
    if cmd == "status":
        _print(service.sources_status(store).model_dump(mode="json"))
    elif cmd == "search":
        live = True if a.live else False if a.local else None
        _print(service.search_legal(store, a.query, relevant_date=a.date, limit=a.limit, live=live).model_dump(mode="json"))
    elif cmd == "get":
        _print(service.get_legal_document(store, a.document_id, a.locator, a.as_of).model_dump(mode="json"))
    return 0


def _sync(ap: argparse.ArgumentParser, a: argparse.Namespace, store: Store) -> int:
    from prawnik_mcp.sync import BulkLimits, sync_corpus, sync_source

    if a.source:
        params = {k: v for k, v in {
            "query": a.query, "court_type": a.court_type, "since": a.since, "until": a.until,
            "acts": a.act or None, "celex": a.celex or None}.items() if v}
        limits = BulkLimits(limit=a.limit, max_bytes=int(a.max_gb * 1024**3) if a.max_gb else None,
                            resume=not a.no_resume)
        try:
            res = sync_source(store, a.source, params, limits, progress=lambda m: print(m, file=sys.stderr))
        except KeyError as e:
            ap.error(str(e))
        except KeyboardInterrupt:
            print("interrupted — progress is checkpointed; run the same command again to resume", file=sys.stderr)
            return 130
        _print(res.model_dump(mode="json"))
        return 0 if res.ok else 1
    fixtures = None
    if a.offline:
        fixtures = Path(a.fixtures) if a.fixtures else REPO_FIXTURES
        if not fixtures.is_dir():
            ap.error("--offline needs --fixtures PATH (recorded samples are not shipped in the wheel)")
    rep = sync_corpus(store, offline_fixtures=fixtures)
    _print(rep.model_dump(mode="json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
