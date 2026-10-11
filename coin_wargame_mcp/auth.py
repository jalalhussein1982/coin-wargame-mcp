"""Bearer-token verification for Streamable HTTP mode.

The server has no personal data; HTTP mode exists so two hosts can share a game and
so the benchmark can drive a remote instance. The verifier keeps the SDK's
``TokenVerifier`` shape and accepts two token kinds, both configured by environment:

* ``OAUTH_STATIC_TOKENS``: JSON ``{"<token>": {"client_id": "...", "scopes": ["wargame:play"]}}``
  for demos and tests.
* HS256 JWTs signed with ``OAUTH_SHARED_SECRET`` (at least 32 bytes) whose ``iss`` matches
  ``OAUTH_ISSUER_URL``, whose ``aud`` is this server's resource URL, and which carry a numeric
  ``exp`` in the future and a ``scope`` claim including the required scope.

Anything else returns ``None`` (never raises), and the SDK answers 401 with a
``resource_metadata`` pointer. Scope enforcement is ``AuthSettings.required_scopes``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier

SCOPE = "wargame:play"
MIN_SECRET_BYTES = 32


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class JwtVerifier(TokenVerifier):
    def __init__(self, issuer: str, secret: str | None = None, static_tokens: dict | None = None,
                 resource: str | None = None) -> None:
        if secret is not None and len(secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"OAUTH_SHARED_SECRET must be at least {MIN_SECRET_BYTES} bytes; got {len(secret.encode())}")
        self.issuer = issuer.rstrip("/")
        self.secret = secret
        self.static_tokens = {str(k): dict(v) for k, v in (static_tokens or {}).items()}
        self.resource = resource

    @classmethod
    def from_env(cls, issuer: str, resource: str | None = None, env: dict | None = None) -> JwtVerifier:
        env = os.environ if env is None else env
        raw = env.get("OAUTH_STATIC_TOKENS", "") or "{}"
        try:
            static = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"OAUTH_STATIC_TOKENS is not valid JSON: {e}") from e
        if not isinstance(static, dict):
            raise ValueError("OAUTH_STATIC_TOKENS must be a JSON object mapping token -> {client_id, scopes}")
        return cls(issuer, env.get("OAUTH_SHARED_SECRET") or None, static, resource)

    def _static(self, token: str) -> AccessToken | None:
        for known, info in self.static_tokens.items():
            if hmac.compare_digest(known.encode(), token.encode()):
                return AccessToken(token=token, client_id=str(info.get("client_id", "static")),
                                   scopes=[str(x) for x in info.get("scopes", [SCOPE])], resource=self.resource)
        return None

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not token:
            return None
        found = self._static(token)
        if found is not None:
            return found
        if not self.secret:
            return None
        try:
            return self._verify_jwt(token)
        except Exception:  # noqa: BLE001 - any malformed token is a 401, never a 500
            return None

    def _verify_jwt(self, token: str) -> AccessToken | None:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header: Any = json.loads(_b64url_decode(header_b64))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
        expected = hmac.new(self.secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        claims: Any = json.loads(_b64url_decode(payload_b64))
        if not isinstance(claims, dict):
            return None
        if str(claims.get("iss", "")).rstrip("/") != self.issuer:
            return None
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or time.time() > exp:
            return None
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if self.resource is None or self.resource not in [str(a) for a in auds if a is not None]:
            return None
        scopes = claims.get("scope", "")
        scopes = scopes.split() if isinstance(scopes, str) else [str(x) for x in scopes]
        return AccessToken(token=token, client_id=str(claims.get("client_id") or claims.get("sub") or "jwt"),
                           scopes=scopes, expires_at=int(exp), resource=self.resource,
                           subject=str(claims["sub"]) if claims.get("sub") is not None else None, claims=claims)


_AUTO = object()


def mint_hs256(secret: str, issuer: str, subject: str, scopes: list[str], ttl: int = 3600, *,
               aud: str | None = None, exp: Any = _AUTO) -> str:
    """Helper for tests and demos: a signed HS256 token. ``exp=None`` omits the claim."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims: dict[str, Any] = {"iss": issuer, "sub": subject, "scope": " ".join(scopes)}
    if aud is not None:
        claims["aud"] = aud
    if exp is _AUTO:
        claims["exp"] = int(time.time()) + ttl
    elif exp is not None:
        claims["exp"] = exp
    payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"
