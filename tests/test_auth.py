"""Bearer auth in HTTP mode: no token -> 401 with resource_metadata; a good token works."""

from __future__ import annotations

import httpx
import pytest

from coin_wargame_mcp.auth import SCOPE, JwtVerifier, mint_hs256
from mcp import Client
from tests.test_http import HEADERS, INIT, Served, free_port, loopback_security

ISSUER = "https://auth.example.test"


@pytest.fixture
def authed_url():
    from coin_wargame_mcp.server import authed_server

    port = free_port()
    resource = f"http://127.0.0.1:{port}/mcp"
    verifier = JwtVerifier(ISSUER, secret="s3cret", static_tokens={"demo-token": {"client_id": "demo", "scopes": [SCOPE]}},
                           resource=resource)
    app = authed_server(ISSUER, resource, verifier).streamable_http_app(transport_security=loopback_security())
    with Served(app, port):
        yield resource


@pytest.mark.anyio
async def test_missing_token_gets_401_with_resource_metadata(authed_url: str):
    async with httpx.AsyncClient() as http:
        r = await http.post(authed_url, json=INIT, headers=HEADERS)
        assert r.status_code == 401
        www = r.headers.get("www-authenticate", "")
        assert "resource_metadata" in www and ".well-known/oauth-protected-resource" in www
        r = await http.post(authed_url, json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401
        meta = await http.get(authed_url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp"))
        assert meta.status_code == 200 and ISSUER in meta.text


@pytest.mark.anyio
async def test_static_and_jwt_tokens_are_accepted(authed_url: str):
    from mcp.client.streamable_http import streamable_http_client

    for token in ("demo-token", mint_hs256("s3cret", ISSUER, "alice", [SCOPE])):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(authed_url, http_client=http)) as c:
                assert len((await c.list_tools()).tools) == 17


@pytest.mark.anyio
async def test_verifier_rejects_bad_issuer_signature_scope_and_expiry():
    v = JwtVerifier(ISSUER, secret="s3cret", resource="r")
    assert (await v.verify_token(mint_hs256("s3cret", ISSUER, "a", [SCOPE]))).scopes == [SCOPE]
    assert await v.verify_token(mint_hs256("other", ISSUER, "a", [SCOPE])) is None
    assert await v.verify_token(mint_hs256("s3cret", "https://else.test", "a", [SCOPE])) is None
    assert await v.verify_token(mint_hs256("s3cret", ISSUER, "a", [SCOPE], ttl=-10)) is None
    assert await v.verify_token("not.a.jwt") is None and await v.verify_token("garbage") is None
    # Scope is enforced by AuthSettings.required_scopes at the HTTP layer; the verifier reports what it saw.
    assert (await v.verify_token(mint_hs256("s3cret", ISSUER, "a", ["other"]))).scopes == ["other"]
    assert await JwtVerifier(ISSUER).verify_token(mint_hs256("s3cret", ISSUER, "a", [SCOPE])) is None  # no secret configured
