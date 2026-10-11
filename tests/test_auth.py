"""Bearer auth in HTTP mode: no token -> 401 with resource_metadata; a good token works."""

from __future__ import annotations

import httpx
import pytest

from coin_wargame_mcp.auth import SCOPE, JwtVerifier, mint_hs256
from mcp import Client
from tests.test_http import HEADERS, INIT, Served, free_port

ISSUER = "https://auth.example.test"
SECRET = "s3cret-" * 6  # 42 bytes


@pytest.fixture
def authed_url():
    from coin_wargame_mcp.server import authed_server

    port = free_port()
    resource = f"http://127.0.0.1:{port}/mcp"
    from coin_wargame_mcp.server import loopback_security

    verifier = JwtVerifier(ISSUER, secret=SECRET, static_tokens={"demo-token": {"client_id": "demo", "scopes": [SCOPE]}},
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

    for token in ("demo-token", mint_hs256(SECRET, ISSUER, "alice", [SCOPE], aud=authed_url)):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(authed_url, http_client=http)) as c:
                assert len((await c.list_tools()).tools) == 17


@pytest.mark.anyio
async def test_verifier_rejects_bad_issuer_signature_scope_and_expiry():
    v = JwtVerifier(ISSUER, secret=SECRET, resource="r")
    good = dict(aud="r")
    assert (await v.verify_token(mint_hs256(SECRET, ISSUER, "a", [SCOPE], **good))).scopes == [SCOPE]
    assert await v.verify_token(mint_hs256("other-" * 6, ISSUER, "a", [SCOPE], **good)) is None
    assert await v.verify_token(mint_hs256(SECRET, "https://else.test", "a", [SCOPE], **good)) is None
    assert await v.verify_token(mint_hs256(SECRET, ISSUER, "a", [SCOPE], ttl=-10, **good)) is None
    assert await v.verify_token("not.a.jwt") is None and await v.verify_token("garbage") is None
    # Scope is enforced by AuthSettings.required_scopes at the HTTP layer; the verifier reports what it saw.
    assert (await v.verify_token(mint_hs256(SECRET, ISSUER, "a", ["other"], **good))).scopes == ["other"]
    assert await JwtVerifier(ISSUER).verify_token(mint_hs256(SECRET, ISSUER, "a", [SCOPE], **good)) is None  # no secret


@pytest.mark.anyio
async def test_malformed_tokens_are_rejected_not_crashes():
    v = JwtVerifier(ISSUER, secret=SECRET, resource="r")
    for bad in ("W10.e30.e30",            # header is a JSON list
                "e30.W10.e30",            # claims is a JSON list
                "e30.e30.e30",            # no alg
                "...", "", "a.b", "a.b.c.d", "\x00.\x00.\x00"):
        assert await v.verify_token(bad) is None, bad


@pytest.mark.anyio
async def test_exp_and_aud_are_required():
    secret = SECRET
    v = JwtVerifier(ISSUER, secret=secret, resource="http://127.0.0.1:1/mcp")
    good = mint_hs256(secret, ISSUER, "a", [SCOPE], aud="http://127.0.0.1:1/mcp")
    assert (await v.verify_token(good)) is not None
    assert await v.verify_token(mint_hs256(secret, ISSUER, "a", [SCOPE], aud="http://other/mcp")) is None
    assert await v.verify_token(mint_hs256(secret, ISSUER, "a", [SCOPE], aud=None)) is None
    assert await v.verify_token(mint_hs256(secret, ISSUER, "a", [SCOPE], aud="http://127.0.0.1:1/mcp", exp=None)) is None
    assert await v.verify_token(mint_hs256(secret, ISSUER, "a", [SCOPE], aud="http://127.0.0.1:1/mcp", exp="soon")) is None


def test_short_or_empty_secret_is_refused_at_construction():
    with pytest.raises(ValueError, match="32"):
        JwtVerifier(ISSUER, secret="short", resource="r")
    JwtVerifier(ISSUER, secret=None, resource="r")  # static-token only is allowed


def test_http_settings_builder_fails_fast_and_derives_hosts(monkeypatch):
    from coin_wargame_mcp.server import http_settings, loopback_security

    sec = loopback_security()
    assert "127.0.0.1:*" in sec.allowed_hosts and "http://localhost:*" in sec.allowed_origins
    # --no-auth: loopback regardless of --host.
    srv, host, security = http_settings(host="0.0.0.0", port=9000, no_auth=True, public_url=None, env={})
    assert host == "127.0.0.1" and security.allowed_hosts == sec.allowed_hosts
    # auth requested but nothing to verify with: refuse to start.
    with pytest.raises(SystemExit):
        http_settings(host="0.0.0.0", port=9000, no_auth=False, public_url=None,
                      env={"OAUTH_ISSUER_URL": ISSUER})
    # non-loopback host needs a public URL.
    with pytest.raises(SystemExit):
        http_settings(host="0.0.0.0", port=9000, no_auth=False, public_url=None,
                      env={"OAUTH_ISSUER_URL": ISSUER, "OAUTH_SHARED_SECRET": "x" * 32})
    srv, host, security = http_settings(host="0.0.0.0", port=9000, no_auth=False,
                                        public_url="https://wargame.example.com/mcp",
                                        env={"OAUTH_ISSUER_URL": ISSUER, "OAUTH_SHARED_SECRET": "x" * 32})
    assert host == "0.0.0.0"
    assert "wargame.example.com" in security.allowed_hosts and "wargame.example.com:*" in security.allowed_hosts
    assert "https://wargame.example.com" in security.allowed_origins
    assert str(srv.settings.auth.resource_server_url) == "https://wargame.example.com/mcp"
    assert srv.settings.auth.validate_token_resource is True
    # no auth env at all and no --no-auth: refuse.
    with pytest.raises(SystemExit):
        http_settings(host="127.0.0.1", port=9000, no_auth=False, public_url=None, env={})
