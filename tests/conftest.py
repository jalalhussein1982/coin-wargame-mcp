"""Shared fixtures: a temporary SQLite store per test and an in-memory MCP client."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the server at a fresh SQLite file so tests never share games."""
    monkeypatch.setenv("COIN_WARGAME_DB", str(tmp_path / "games.db"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OAUTH_ISSUER_URL", raising=False)
    yield
