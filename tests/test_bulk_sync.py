"""Bulk sync (SAOS dump / search) with checkpoints — offline, mocked transport, real judgment fixtures."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from prawnik_mcp.connectors.base import BulkLimits
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.store import Store
from prawnik_mcp.sync import sync_source

FX = Path(__file__).parent / "fixtures" / "raw"


def _item(sid: int) -> dict:
    return json.loads((FX / f"saos_{sid}.json").read_bytes())["data"]


PAGES = {0: [_item(31345), _item(266474)], 1: [_item(361247)], 2: []}


def _client(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        q = parse_qs(urlsplit(str(req.url)).query)
        page = int(q["pageNumber"][0])
        items = PAGES.get(page, [])
        links = [{"rel": "self", "href": str(req.url)}] + ([{"rel": "next", "href": "x"}] if items else [])
        return httpx.Response(200, json={"items": items, "links": links})

    return PoliteClient(transport=httpx.MockTransport(handler), min_delay=0, sleep=lambda s: None)


def test_dump_resume_after_limit_mid_page(tmp_path):
    store, calls = Store(tmp_path), []
    params = {"since": "2000-01-01", "until": "2026-09-01"}
    r1 = sync_source(store, "saos", params, BulkLimits(limit=1), client=_client(calls))
    assert r1.counts["stored"] == 1 and store.stats()["judgments"] == 1
    assert "/dump/judgments" in calls[0]
    r2 = sync_source(store, "saos", params, BulkLimits(), client=_client(calls))
    assert r2.ok and r2.counts["stored"] == 2  # rest of page 0 + page 1; nothing duplicated
    assert store.stats()["judgments"] == 3
    state = store.list_sync_state("saos")[0]
    assert state["done"] and state["cursor"] == "2"
    r3 = sync_source(store, "saos", params, BulkLimits(), client=_client(calls))
    assert r3.ok and any("already complete" in w for w in r3.warnings)


def test_dump_court_type_filter_is_applied_locally(tmp_path):
    store, calls = Store(tmp_path), []
    r = sync_source(store, "saos", {"court_type": "SUPREME"}, BulkLimits(), client=_client(calls))
    assert r.ok and r.counts["stored"] == 0  # fixtures are common-court judgments
    assert store.stats()["judgments"] == 0


def test_bulk_respects_max_bytes(tmp_path):
    store, calls = Store(tmp_path), []
    r = sync_source(store, "saos", {}, BulkLimits(max_bytes=1), client=_client(calls))
    assert any("max-gb" in w for w in r.warnings) and store.stats()["judgments"] == 0
