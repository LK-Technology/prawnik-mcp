"""Offline tests of the polite HTTP client (httpx.MockTransport, no network)."""

import socket

import httpx
import pytest

from prawnik_mcp.connectors.http import (
    USER_AGENT,
    BlockedUrl,
    NotFoundUpstream,
    PoliteClient,
    SourceUnavailable,
)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make_client(sleeps):
    def make(handler, **kw):
        return PoliteClient(transport=httpx.MockTransport(handler), sleep=sleeps.append, min_delay=0, **kw)
    return make


def test_retry_after_and_backoff(make_client, sleeps):
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": 1})

    r = make_client(handler).get("https://api.sejm.gov.pl/eli/acts/DU/1964/93", accept="application/json")
    assert r.status == 200 and len(calls) == 3
    assert sleeps[:2] == [7.0, 2.0]  # Retry-After honoured, then exponential backoff
    assert calls[0].headers["user-agent"] == USER_AGENT
    assert calls[0].headers["accept"] == "application/json"


def test_retry_after_too_long_gives_up(make_client):
    with pytest.raises(SourceUnavailable):
        make_client(lambda r: httpx.Response(429, headers={"Retry-After": "9999"})).get("https://www.saos.org.pl/x")


def test_5xx_is_unavailable_not_404(make_client):
    with pytest.raises(SourceUnavailable) as e:
        make_client(lambda r: httpx.Response(500), max_retries=2).get("https://www.saos.org.pl/api/x")
    assert e.value.http_status == 500 and not isinstance(e.value, NotFoundUpstream)


def test_404_is_not_found(make_client):
    with pytest.raises(NotFoundUpstream) as e:
        make_client(lambda r: httpx.Response(404)).get("https://www.saos.org.pl/api/judgments/1")
    assert e.value.http_status == 404
    assert not isinstance(e.value, SourceUnavailable)


def test_202_empty_challenge_is_unavailable(make_client):
    with pytest.raises(SourceUnavailable):
        make_client(lambda r: httpx.Response(202, content=b"")).get("https://publications.europa.eu/x")


def test_network_error_is_unavailable(make_client):
    def handler(req):
        raise httpx.ConnectError("boom")
    with pytest.raises(SourceUnavailable):
        make_client(handler, max_retries=1).get("https://www.saos.org.pl/x")


@pytest.mark.parametrize("url", ["https://eur-lex.europa.eu/x", "http://api.sejm.gov.pl/x", "https://api.sejm.gov.pl:8443/x"])
def test_allowlist(make_client, url):
    with pytest.raises(BlockedUrl):
        make_client(lambda r: httpx.Response(200, content=b"x")).get(url)


@pytest.mark.parametrize("target", ["https://evil.example/", "https://169.254.169.254/latest"])
def test_redirect_off_allowlist_blocked(make_client, target):
    with pytest.raises(BlockedUrl):
        make_client(lambda r: httpx.Response(302, headers={"Location": target})).get("https://api.sejm.gov.pl/x")


def test_redirect_within_allowlist_upgraded_to_https(make_client):
    def handler(req):
        if req.url.path == "/a":
            return httpx.Response(303, headers={"Location": "http://publications.europa.eu/b"})
        assert req.url.scheme == "https"
        return httpx.Response(200, content=b"<html>ok</html>")

    r = make_client(handler).get("https://publications.europa.eu/a")
    assert r.url == "https://publications.europa.eu/b"
    assert r.redirects == ["https://publications.europa.eu/a"]


def test_max_size(make_client):
    with pytest.raises(SourceUnavailable):
        make_client(lambda r: httpx.Response(200, content=b"x" * 2000), max_bytes=1000).get("https://www.saos.org.pl/x")


def test_private_ip_resolution_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with PoliteClient(resolve_dns=True) as c:
        with pytest.raises(BlockedUrl):
            c.check_url("https://api.sejm.gov.pl/x")


def test_post_form_and_per_host_delay():
    seen = []

    def handler(req):
        seen.append((req.method, req.headers.get("content-type", ""), req.content))
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []
    c = PoliteClient(transport=httpx.MockTransport(handler), sleep=sleeps.append, clock=lambda: 0.0,
                     allowlist={"www.saos.org.pl"}, host_delays={"www.saos.org.pl": 2.0})
    c.post("https://www.saos.org.pl/api/x", data={"q": "a"})
    c.get("https://www.saos.org.pl/api/y")
    assert seen[0][0] == "POST" and "application/x-www-form-urlencoded" in seen[0][1] and seen[0][2] == b"q=a"
    assert seen[1][0] == "GET"
    assert sleeps == [2.0]  # waited the per-host delay (0.5 req/s), not the global default of 1 s


def test_default_allowlist_comes_from_catalog():
    c = PoliteClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x")))
    assert {"api.sejm.gov.pl", "www.saos.org.pl", "publications.europa.eu"} <= c.allowlist
    assert "orzeczenia.ms.gov.pl" not in c.allowlist  # research-only sources (portal_sp) are not contacted
