"""Model providers for the benchmark: one interface, a deterministic stub, and Anthropic.

A provider is asked, given the conversation so far and the MCP tool list, what to do
next: call a tool (``ToolCall``) or stop (``Final``). The runner executes the call
through the MCP client and feeds the result back. The provider never sees the engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class Final:
    text: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, i: int, o: int) -> None:
        self.input_tokens += i
        self.output_tokens += o


class Provider(Protocol):
    name: str
    usage: Usage

    def choose(self, messages: list[dict], tools: list[dict]) -> ToolCall | Final: ...


# ── stub ─────────────────────────────────────────────────────────────────────────────

PREFERRED = {
    "AQ_NETWORK": ["propaganda", "smuggling_operation", "ambush", "assassination", "taxation"],
    "ISIS_TERRITORIAL": ["territorial_seizure", "taxation", "shadow_court", "complex_attack", "propaganda"],
    "TALIBAN_ADAPTIVE": ["intimidation", "shadow_court", "hit_and_run", "recruitment_drive", "propaganda"],
    "government": ["patrol", "tribal_engagement", "service_provision", "checkpoint_network", "intelligence_operation",
                   "media_campaign"],
}


@dataclass
class StubProvider:
    """Rule-based baseline: recommend_doctrine, then the first legal action of a preferred type.

    Deterministic given the game, so stub-vs-stub on one seed is itself replayable.
    The policy is deliberately simple; it is the floor a host model should beat."""

    name: str = "stub"
    usage: Usage = field(default_factory=Usage)
    _doctrine: str | None = None

    def choose(self, messages: list[dict], tools: list[dict]) -> ToolCall | Final:
        state = _bench_state(messages)
        faction = state["faction"]
        if faction == "insurgent" and not state.get("doctrine_asked"):
            return ToolCall("recommend_doctrine", {"game_id": state["game_id"]})
        if not state.get("legal"):
            return ToolCall("list_legal_actions", {"game_id": state["game_id"], "faction": faction, "limit": 500})
        prefs = PREFERRED["government"] if faction == "government" else PREFERRED.get(state.get("doctrine") or "AQ_NETWORK")
        legal = state["legal"]
        chosen = None
        for pref in prefs:
            for a in legal:
                if a["action_type"] == pref and a["source_province"] in state.get("hot", [a["source_province"]]):
                    chosen = a
                    break
            if chosen:
                break
        if chosen is None:
            for pref in prefs:
                chosen = next((a for a in legal if a["action_type"] == pref), None)
                if chosen:
                    break
        if chosen is None:
            chosen = legal[0]
        action = {"action_type": chosen["action_type"], "source_province": chosen["source_province"],
                  "target_province": chosen["target_province"], "forces_committed": chosen["min_forces"],
                  "resources_spent": 0}
        rationale = (f"{state.get('doctrine') or 'stub policy'}: first legal {chosen['action_type']} "
                     f"from {chosen['source_province']}")
        return ToolCall("submit_action", {"game_id": state["game_id"], "faction": faction, "action": action,
                                          "rationale": rationale})


def _bench_state(messages: list[dict]) -> dict:
    """The runner stores a JSON blob in the first user message; tool results follow as user messages."""
    state = json.loads(messages[0]["content"]) if isinstance(messages[0]["content"], str) else dict(messages[0]["content"])
    for m in messages[1:]:
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        for block in m["content"]:
            if block.get("type") != "tool_result":
                continue
            name, payload = block.get("_tool"), block.get("_json")
            if name == "recommend_doctrine" and payload:
                state["doctrine"], state["doctrine_asked"] = payload.get("doctrine"), True
            elif name == "list_legal_actions" and payload:
                state["legal"] = payload.get("actions", [])
            elif name == "situation_brief" and payload:
                state["hot"] = [p for p in ("hodh_ech_chargui", "hodh_el_gharbi", "guidimaka", "assaba")]
    return state


# ── Anthropic ────────────────────────────────────────────────────────────────────────


@dataclass
class AnthropicProvider:
    """Messages API tool-use loop. Importable without a key; fails only when called.

    Each ``choose`` sends the system prompt (the MCP prompt text), the conversation and
    the MCP tool list converted to Anthropic tool definitions, and returns the first
    ``tool_use`` block or the final text."""

    model: str = DEFAULT_ANTHROPIC_MODEL
    system: str = ""
    max_tokens: int = 1024
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    _client: Any = None

    def __post_init__(self) -> None:
        self.name = self.name or self.model

    def _ensure_client(self):
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set; the Anthropic provider needs it (the stub does not)")
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("install the bench extra: uv sync --all-extras") from e
            self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def to_anthropic_tools(tools: list[dict]) -> list[dict]:
        return [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]}
                for t in tools]

    def choose(self, messages: list[dict], tools: list[dict]) -> ToolCall | Final:
        client = self._ensure_client()
        resp = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=self.system or "You play a wargame through tools.",
            messages=[_strip_private(m) for m in messages], tools=self.to_anthropic_tools(tools),
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )
        self.usage.add(resp.usage.input_tokens, resp.usage.output_tokens)
        for block in resp.content:
            if block.type == "tool_use":
                messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
                return ToolCall(block.name, dict(block.input), id=block.id)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        messages.append({"role": "assistant", "content": text or "(no text)"})
        return Final(text)


def _strip_private(m: dict) -> dict:
    """Drop the runner's private ``_tool``/``_json`` annotations before sending to the API."""
    if isinstance(m.get("content"), list):
        return {"role": m["role"], "content": [{k: v for k, v in b.items() if not k.startswith("_")} for b in m["content"]]}
    return m


def make_provider(spec: str, model: str = DEFAULT_ANTHROPIC_MODEL, system: str = "") -> Provider:
    if spec == "stub":
        return StubProvider()
    if spec in ("anthropic", "claude"):
        return AnthropicProvider(model=model, system=system)
    raise ValueError(f"unknown provider {spec!r}; use 'stub' or 'anthropic'")
