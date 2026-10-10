"""Stub vs stub over MCP: completes, writes rows, no illegal attempts, every game replays."""

from __future__ import annotations

import json

import pytest

from bench import providers
from bench.__main__ import run
from bench.scoring import summarize


@pytest.mark.anyio
async def test_stub_vs_stub_three_seeds(tmp_path):
    from coin_wargame_mcp.server import mcp

    out = tmp_path / "bench.jsonl"
    await run(mcp, [1, 2, 3], "stub", "stub", "unused", max_turns=6, side="red", out=out)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert [r["seed"] for r in rows] == [1, 2, 3]
    for r in rows:
        assert r["illegal_attempts"] == 0
        assert r["reproduced"] is True
        assert r["stalls"] == 0
        assert r["turns"] >= 6 or r["winner"] is not None
        assert r["performance_score"] is not None and r["approach"]
        assert r["model"] == "stub" and r["side"] == "insurgent" and r["opponent"] == "stub"
    table = summarize(out)
    assert table.splitlines()[0].startswith("| model | side | games |")
    assert "| stub | insurgent | 3 |" in table


def test_anthropic_provider_is_importable_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = providers.AnthropicProvider()
    assert p.model == "claude-sonnet-5-5" and p.name == "claude-sonnet-5-5"
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        p.choose([{"role": "user", "content": "{}"}], [])
    tools = providers.AnthropicProvider.to_anthropic_tools([
        {"name": "x", "description": "d", "input_schema": {"type": "object", "properties": {}}}])
    assert tools == [{"name": "x", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    with pytest.raises(ValueError):
        providers.make_provider("gemini")
