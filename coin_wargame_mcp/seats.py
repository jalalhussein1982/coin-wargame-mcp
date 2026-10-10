"""Seat tokens: who may move a faction.

A seat is ``host`` (the caller who created the game plays it; no token) or
``open`` (another host must present the token minted at ``new_game``). The
check is deliberately simple: the game has no personal data, and the token's
only job is to keep two hosts sharing one game from moving each other's pieces.
"""

from __future__ import annotations

import hmac
import secrets

from mcp.server.mcpserver.exceptions import ToolError

from .games import GameRecord

FACTIONS = ("government", "insurgent")


def issue() -> str:
    """A fresh 32-hex seat token."""
    return secrets.token_hex(16)


def check(rec: GameRecord, faction: str, token: str | None) -> None:
    """Raise ``ToolError`` when ``faction``'s seat is open and ``token`` does not match."""
    if faction not in FACTIONS:
        raise ToolError(f"unknown faction {faction!r}; use 'government' or 'insurgent'")
    if rec.seats.get(faction, "host") != "open":
        return
    expected = rec.seat_tokens.get(faction)
    if not token or not expected or not hmac.compare_digest(expected, token):
        raise ToolError(
            f"the {faction} seat is open; present its seat_token (issued by new_game) to act for it"
        )
