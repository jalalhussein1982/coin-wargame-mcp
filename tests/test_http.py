"""Streamable HTTP smoke: --no-auth on loopback serves tools/list; a foreign Host is refused."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from mcp import Client

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """Run an ASGI app under uvicorn in a thread for the duration of a test."""

    def __init__(self, app, port: int):
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("uvicorn did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture
def no_auth_url():
    from coin_wargame_mcp.server import http_settings

    port = free_port()
    server, host, security = http_settings(host="0.0.0.0", port=port, no_auth=True, public_url=None, env={})
    assert host == "127.0.0.1"
    app = server.streamable_http_app(transport_security=security)
    with Served(app, port):
        yield f"http://127.0.0.1:{port}/mcp"


@pytest.mark.anyio
async def test_no_auth_loopback_serves_tools(no_auth_url: str):
    async with Client(no_auth_url) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        assert names[0] == "new_game" and len(names) == 17
        g = (await c.call_tool("new_game", {"seed": 4})).structured_content
        assert g["seed"] == 4


@pytest.mark.anyio
async def test_foreign_host_header_is_refused(no_auth_url: str):
    async with httpx.AsyncClient() as http:
        r = await http.post(no_auth_url, json=INIT, headers={**HEADERS, "Host": "evil.example"})
        assert r.status_code == 421
        r = await http.post(no_auth_url, json=INIT, headers={**HEADERS, "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = await http.post(no_auth_url, json=INIT, headers=HEADERS)
        assert r.status_code == 200
