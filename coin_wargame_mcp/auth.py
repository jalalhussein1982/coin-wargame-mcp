"""Bearer-token verification for Streamable HTTP mode.

The server has no personal data; HTTP mode exists so two hosts can share a game and
so the benchmark can drive a remote instance. The verifier therefore keeps the usual
``TokenVerifier`` shape and accepts two token kinds, both configured by environment:

* ``OAUTH_STATIC_TOKENS``: JSON ``{"<token>": {"client_id": "...", "scopes": ["wargame:play"]}}``
  for demos and tests.
* HS256 JWTs signed with ``OAUTH_SHARED_SECRET`` whose ``iss`` matches
  ``OAUTH_ISSUER_URL`` and whose ``scope`` claim includes the required scope.

Anything else is rejected and the SDK answers 401 with a ``resource_metadata`` pointer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from mcp.server.auth.provider import AccessToken, TokenVerifier

SCOPE = "wargame:play"


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class JwtVerifier(TokenVerifier):
    def __init__(self, issuer: str, secret: str | None = None, static_tokens: dict | None = None,
                 resource: str | None = None) -> None:
        self.issuer = issuer.rstrip("/")
        self.secret = secret
        self.static_tokens = static_tokens or {}
        self.resource = resource

    @classmethod
    def from_env(cls, issuer: str, resource: str | None = None) -> JwtVerifier:
        static = json.loads(os.environ.get("OAUTH_STATIC_TOKENS", "{}") or "{}")
        return cls(issuer, os.environ.get("OAUTH_SHARED_SECRET"), static, resource)

    async def verify_token(self, token: str) -> AccessToken | None:
        if token in self.static_tokens:
            info = self.static_tokens[token]
            return AccessToken(token=token, client_id=info.get("client_id", "static"),
                               scopes=list(info.get("scopes", [SCOPE])), resource=self.resource)
        if not self.secret:
            return None
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            header = json.loads(_b64url_decode(header_b64))
            if header.get("alg") != "HS256":
                return None
            expected = hmac.new(self.secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
                return None
            claims = json.loads(_b64url_decode(payload_b64))
        except (ValueError, json.JSONDecodeError):
            return None
        if str(claims.get("iss", "")).rstrip("/") != self.issuer:
            return None
        exp = claims.get("exp")
        if exp is not None and time.time() > float(exp):
            return None
        scopes = claims.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else list(scopes)
        return AccessToken(token=token, client_id=str(claims.get("client_id") or claims.get("sub") or "jwt"),
                           scopes=scopes, expires_at=int(exp) if exp else None,
                           resource=self.resource, subject=claims.get("sub"), claims=claims)


def mint_hs256(secret: str, issuer: str, subject: str, scopes: list[str], ttl: int = 3600) -> str:
    """Helper for tests and demos: a signed HS256 token the verifier accepts."""
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).rstrip(b"=").decode()
    header = enc({"alg": "HS256", "typ": "JWT"})
    payload = enc({"iss": issuer, "sub": subject, "scope": " ".join(scopes), "exp": int(time.time()) + ttl})
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
