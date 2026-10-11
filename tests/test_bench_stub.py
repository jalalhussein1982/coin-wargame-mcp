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
    await run(mcp, [1, 2, 3], "stub", "stub", "unused", max_turns=12, side="red", out=out)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert [r["seed"] for r in rows] == [1, 2, 3]
    for r in rows:
        assert r["illegal_attempts"] == 0
        assert r["reproduced"] is True
        assert r["stalls"] == 0
        assert r["turns"] >= 12 or r["winner"] is not None
        assert r["tool_calls"] <= 3 * 12  # recommend + list + act per turn, never a retry loop
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


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=False):
        return {k: v for k, v in self.__dict__.items() if not (exclude_none and v is None)}


class _Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason
        self.usage = _Block(input_tokens=10, output_tokens=5)


class _FakeMessages:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def test_anthropic_provider_handles_stop_reasons(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    p = providers.AnthropicProvider(system="s")
    truncated = _Resp([_Block(type="tool_use", id="t1", name="submit_action", input={"a": 1}, cache_control=None)], "max_tokens")
    complete = _Resp([_Block(type="text", text="thinking", cache_control=None),
                      _Block(type="tool_use", id="t2", name="get_state", input={"game_id": "g"}, cache_control=None)], "tool_use")
    p._client = _Block(messages=_FakeMessages([truncated, complete]))
    messages = [{"role": "user", "content": "{}"}]
    choice = p.choose(messages, [])
    # The truncated tool_use was not executed; the call was retried with a larger budget.
    assert isinstance(choice, providers.ToolCall) and choice.name == "get_state" and choice.id == "t2"
    calls = p._client.messages.calls
    assert calls[0]["max_tokens"] == 4096 and calls[1]["max_tokens"] > calls[0]["max_tokens"]
    assert messages[-1]["role"] == "assistant"
    assert all("cache_control" not in b for b in messages[-1]["content"]), "None fields are dropped"
    assert p.usage.input_tokens == 20

    p._client = _Block(messages=_FakeMessages([_Resp([_Block(type="text", text="I decline.", cache_control=None)], "refusal")]))
    choice = p.choose([{"role": "user", "content": "{}"}], [])
    assert isinstance(choice, providers.Final) and "refusal" in choice.text

    p._client = _Block(messages=_FakeMessages([_Resp([_Block(type="text", text="", cache_control=None)], "pause_turn"),
                                               _Resp([_Block(type="text", text="done", cache_control=None)], "end_turn")]))
    choice = p.choose([{"role": "user", "content": "{}"}], [])
    assert isinstance(choice, providers.Final) and choice.text == "done"


def test_stdio_server_env_strips_model_keys(monkeypatch):
    from bench.__main__ import stdio_server

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    env = stdio_server().env
    assert "ANTHROPIC_API_KEY" not in env and "GEMINI_API_KEY" not in env
