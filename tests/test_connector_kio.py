"""KIO connector (orzeczenia.uzp.gov.pl) — offline, httpx.MockTransport serving recorded responses."""

import re
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from prawnik_mcp.connectors.base import BulkLimits
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.connectors.kio import KioConnector
from prawnik_mcp.contracts import SourceKind
from prawnik_mcp.parsers.kio import content_to_text, content_url, details_url
from prawnik_mcp.store import Store

FX = Path(__file__).parent / "fixtures" / "raw" / "kio"
HOST = "orzeczenia.uzp.gov.pl"


def _html(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=(FX / name).read_bytes(), headers={"content-type": "text/html; charset=utf-8"})


def _client(calls: list, *, empty_content: bool = False) -> PoliteClient:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, parse_qs(req.content.decode()) if req.method == "POST" else {}))
        if req.method == "POST" and req.url.path == "/Home/GetResults":
            form = parse_qs(req.content.decode())
            if "Sign" in form:
                return _html("search_sign.html")
            if "Phrase" in form:
                return _html("search_phrase.html")
            page = FX / f"search_day_p{form['Pg'][0]}.html"
            return _html(page.name) if page.exists() else httpx.Response(200, text='<input type="hidden" value="0,0,0,0,0" id="resultCounts" />')
        if m := re.fullmatch(r"/Home/Details/(\d+)", req.url.path):
            f = FX / f"details_{m.group(1)}.html"
            return _html(f.name) if f.exists() else _html("details_404.html", 404)  # real UZP 404 page
        if m := re.fullmatch(r"/Home/ContentHtml/(\d+)", req.url.path):
            f = FX / f"content_{m.group(1)}.html"
            if empty_content or not f.exists():
                return httpx.Response(200, content=b"")  # what UZP answers for an unknown id
            return _html(f.name)
        return httpx.Response(404)

    return PoliteClient(transport=httpx.MockTransport(handler), allowlist={HOST}, min_delay=0, sleep=lambda s: None)


def test_search_maps_hits_snippets_and_case_numbers():
    calls: list = []
    hits = KioConnector().search(_client(calls), "rażąco niska cena", limit=3,
                                 filters={"date_from": "2025-05-15", "date_to": "2025-05-15"})
    method, path, form = calls[0]
    assert (method, path) == ("POST", "/Home/GetResults")
    assert form["Phrase"] == ["rażąco niska cena"] and form["Kind"] == ["KIO"]
    assert form["Dt"] == ["15-05-2025 - 15-05-2025"] and form["Fle"] == ["1"] and form["SCnt"] == ["1"]
    assert [h.document_id for h in hits] == ["kio:28955", "kio:28950", "kio:28947"]
    h = hits[0]
    assert h.kind == "judgment" and h.original_url == details_url(28955)
    assert h.metadata["case_numbers"] == ["KIO 1597/25"] and h.metadata["judgment_date"] == "2025-05-15"
    assert h.metadata["court_type"] == "NATIONAL_APPEAL_CHAMBER" and h.metadata["total"] == 7
    assert "rażąco" in h.snippet and "<" not in h.snippet and len(h.snippet) <= 800


def test_search_by_case_number_returns_joined_cases():
    calls: list = []
    hits = KioConnector().search(_client(calls), "wyrok KIO 1550/25", filters={"case_number": "kio 1550/25"})
    assert calls[0][2]["Sign"] == ["KIO 1550/25"] and "Phrase" not in calls[0][2]
    assert len(hits) == 1 and hits[0].document_id == "kio:28952"
    assert hits[0].metadata["case_numbers"] == ["KIO 1550/25", "KIO 1581/25"]
    assert hits[0].metadata["judgment_type"] == "SENTENCE"


def test_search_skips_foreign_case_numbers_and_court_types_without_requests():
    calls: list = []
    conn = KioConnector()
    assert conn.search(_client(calls), "x", filters={"case_number": "I C 123/20"}) == []
    assert conn.search(_client(calls), "x", filters={"court_type": "COMMON"}) == []
    assert calls == []


def test_search_rejects_unexpected_page():
    def handler(req):
        return httpx.Response(200, text="<html><body>Wyszukiwanie dokumentów. Proszę czekać...</body></html>")

    c = PoliteClient(transport=httpx.MockTransport(handler), allowlist={HOST}, min_delay=0)
    with pytest.raises(ValueError, match="unexpected KIO search response"):
        KioConnector().search(c, "cokolwiek")


def test_fetch_stores_judgment_with_both_snapshots(tmp_path):
    store, calls = Store(tmp_path), []
    conn = KioConnector()
    assert conn.fetch(store, _client(calls), "kio:28952") == "kio:28952"
    assert [c[1] for c in calls] == ["/Home/Details/28952", "/Home/ContentHtml/28952"]

    j = store.get_judgment("kio:28952")
    assert j.court_name == "Krajowa Izba Odwoławcza" and j.court_type == "NATIONAL_APPEAL_CHAMBER"
    assert j.case_numbers == ["KIO 1550/25", "KIO 1581/25"] and str(j.judgment_date) == "2025-05-15"
    assert j.judgment_type == "SENTENCE" and j.original_url == details_url(28952) and not j.data_quality_flags
    assert [x.document_id for x in store.find_judgments_by_case_number("kio  1581/25")] == ["kio:28952"]

    doc = store.get_document("kio:28952")
    assert doc.kind == SourceKind.judgment and doc.snapshot_id == j.snapshot_id
    assert doc.title == "Wyrok – Krajowa Izba Odwoławcza – KIO 1550/25, KIO 1581/25 – 2025-05-15"
    assert doc.metadata["outcome"] == "kio 1550/25: oddalone; kio 1581/25: uwzględnione"
    assert "art. 226 ust. 1 pkt 5" in doc.metadata["pzp_articles"]
    assert "odrzucenie oferty" in doc.metadata["subject_index"]
    # raw snapshots are kept byte-for-byte
    assert store.read_snapshot_bytes(j.snapshot_id) == (FX / "content_28952.html").read_bytes()
    dsnap = store.find_snapshot_by_url(details_url(28952))
    assert dsnap.snapshot_id == doc.metadata["details_snapshot_id"]
    assert store.read_snapshot_bytes(dsnap.snapshot_id) == (FX / "details_28952.html").read_bytes()
    assert store.find_snapshot_by_url(content_url(28952)).snapshot_id == j.snapshot_id

    n = len(calls)
    assert conn.fetch(store, _client(calls), "kio:28952") == "kio:28952"
    assert len(calls) == n  # checkpoint: stored snapshots are re-parsed, not re-fetched
    assert conn.fetch(store, _client(calls), "saos:1") is None


def test_fetch_404_is_not_stored(tmp_path):
    store, calls = Store(tmp_path), []
    with pytest.raises(NotFoundUpstream):
        KioConnector().fetch(store, _client(calls), "kio:99999999")
    assert [c[1] for c in calls] == ["/Home/Details/99999999"]  # text page never requested
    assert store.get_judgment("kio:99999999") is None and store.stats()["snapshots"] == 0


def test_fetch_empty_text_is_unavailable_and_not_stored(tmp_path):
    store, calls = Store(tmp_path), []
    with pytest.raises(SourceUnavailable):
        KioConnector().fetch(store, _client(calls, empty_content=True), "kio:28944")
    assert store.get_judgment("kio:28944") is None and store.stats()["snapshots"] == 0


def test_sync_bulk_checkpoint_and_resume(tmp_path):
    store, calls = Store(tmp_path), []
    conn = KioConnector()
    params = {"since": "2025-05-15", "until": "2025-05-15"}

    r1 = conn.sync_bulk(store, _client(calls), params, BulkLimits(limit=2))
    assert r1.ok and r1.counts["stored"] == 2
    assert {j for j in ("kio:28944", "kio:28952") if store.get_judgment(j)} == {"kio:28944", "kio:28952"}
    assert any("28945" in w and "404" in w for w in r1.warnings)  # listed but missing upstream: not stored
    assert store.get_judgment("kio:28945") is None
    form = calls[0][2]
    assert form["Srt"] == ["date_asc"] and form["Dt"] == ["15-05-2025 - 15-05-2025"] and form["Pg"] == ["1"]
    state = store.list_sync_state("kio")[0]
    assert state["cursor"] == "1" and not state["done"]  # limit hit mid-page: the page is revisited

    calls.clear()
    r2 = conn.sync_bulk(store, _client(calls), params, BulkLimits())
    assert r2.ok and r2.counts["stored"] == 1 and store.get_judgment("kio:28956") is not None
    fetched_details = [c[1] for c in calls if c[1].startswith("/Home/Details/")]
    assert "/Home/Details/28944" not in fetched_details and "/Home/Details/28952" not in fetched_details
    state = store.list_sync_state("kio")[0]
    assert state["done"] and state["cursor"] == "3" and state["items"] == 16
    assert store.stats()["judgments"] == 3

    r3 = conn.sync_bulk(store, _client(calls), params, BulkLimits())
    assert r3.ok and any("already complete" in w for w in r3.warnings)


def test_sync_offline_from_recorded_pairs(tmp_path):
    store = Store(tmp_path)
    res = KioConnector().sync_offline(store, FX.parent)
    assert res.ok and res.counts == {"judgments": 3}, res.errors
    assert {j.document_id for j in store.find_judgments_by_case_number("KIO 1606/25")} == {"kio:28956"}
    src = store.get_source("kio")
    assert src.access_status == "degraded" and "3 KIO rulings" in src.coverage
    assert store.fts_search('"umorzyć postępowanie odwoławcze"', ["judgment"], 10, 0, ["kio"])


def test_html_to_text_keeps_paragraphs():
    text = content_to_text((FX / "content_28944.html").read_bytes())
    lines = text.split("\n")
    assert lines[:3] == ["Sygn. akt: KIO 1404/25", "POSTANOWIENIE", "Warszawa, dnia 15 maja 2025 r."]
    assert "postanawia:" in lines and "1.umorzyć postępowanie odwoławcze," in lines
    assert "Uzasadnienie" in lines
    assert "<" not in text and "‎" not in text and "\n\n" not in text
    # the ruling's own line structure survives (a <br> inside a paragraph is a line break)
    assert "Centrum Realizacji Inwestycji\nw Warszawie" in text
