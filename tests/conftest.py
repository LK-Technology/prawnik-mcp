"""Test configuration: offline by default.

Every test except those marked `online` runs with outbound network connections blocked, so a
connector that accidentally hits a real server fails loudly instead of silently depending on it.
Loopback and Unix sockets (used by the in-process MCP client and subprocess stdio) stay allowed.
"""

import socket

import pytest

_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    if self.family == socket.AF_UNIX:
        return _real_connect(self, address)
    host = address[0] if isinstance(address, tuple) else address
    if host in ("127.0.0.1", "::1", "localhost"):
        return _real_connect(self, address)
    raise RuntimeError(f"network access blocked in offline tests: {address!r} (mark the test @pytest.mark.online)")


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    if request.node.get_closest_marker("online"):
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    # live search / lazy fetch off unless a test enables it explicitly with a mock client
    monkeypatch.setenv("PRAWNIK_MCP_OFFLINE", "1")
