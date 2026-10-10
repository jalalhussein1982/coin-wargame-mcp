"""coin-bench: an MCP client that makes models play the wargame against each other.

    uv run python -m bench --games 3 --seed 1 --red stub --gov stub
    ANTHROPIC_API_KEY=... uv run python -m bench --games 5 --red anthropic --model claude-sonnet-5-5

The harness never imports the engine. It connects to the server over MCP (stdio
subprocess by default, or in-process for tests), creates N games with fixed seeds,
and for each turn asks the active seat's provider for the next tool call given the
seat's play prompt and the tool list, executes it, and loops until victory or
``--max-turns``. One JSONL row per game is written for the side under test
(``--side``), then a Markdown table is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from .providers import DEFAULT_ANTHROPIC_MODEL, Final, Provider, ToolCall, make_provider
from .scoring import summarize

RESULTS_DIR = Path(__file__).resolve().parent / "results"
MAX_STEPS_PER_TURN = 12


@dataclass
class GameRow:
    seed: int
    model: str
    side: str
    opponent: str
    game_id: str
    turns: int
    winner: str | None
    condition: str | None
    performance_score: float | None
    aar_government_score: float | None
    approach: str | None
    illegal_attempts: int
    tool_calls: int
    stalls: int
    reproduced: bool | None
    tokens: dict[str, int]
    seconds: float
    date: str = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0).isoformat())


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


async def _call(client: Client, name: str, args: dict) -> tuple[bool, Any, str]:
    r = await client.call_tool(name, args)
    return bool(r.is_error), r.structured_content, _text(r)


async def play_game(client: Client, seed: int, red: Provider, gov: Provider, max_turns: int,
                    side: str, verbose: bool = False) -> GameRow:
    started = time.monotonic()
    tools = [t.model_dump(mode="json", by_alias=False) for t in (await client.list_tools()).tools]
    for t in tools:
        t["input_schema"] = t.pop("input_schema", None) or t.pop("inputSchema", None)
    prompts = {
        "insurgent": (await client.get_prompt("play_red_team", {"game_id": "GAME"})).messages[0].content.text,
        "government": (await client.get_prompt("play_government", {"game_id": "GAME"})).messages[0].content.text,
    }
    _, created, _ = await _call(client, "new_game", {"seed": seed})
    gid = created["game_id"]
    providers = {"insurgent": red, "government": gov}
    for faction, p in providers.items():
        if hasattr(p, "system"):
            p.system = prompts[faction].replace("GAME", gid)

    illegal = {"insurgent": 0, "government": 0}
    calls = {"insurgent": 0, "government": 0}
    stalls = {"insurgent": 0, "government": 0}
    _, state, _ = await _call(client, "get_state", {"game_id": gid})
    turns_played = 0
    while state["status"] == "active" and state["turn"] <= max_turns:
        faction = state["active_faction"]
        provider = providers[faction]
        messages: list[dict] = [{"role": "user", "content": json.dumps({
            "game_id": gid, "faction": faction, "turn": state["turn"], "phase": state["phase"],
            "instruction": f"It is turn {state['turn']}; you are the {faction}. Act with the tools, then stop.",
        })}]
        acted = False
        for _step in range(MAX_STEPS_PER_TURN):
            choice = provider.choose(messages, tools)
            if isinstance(choice, Final):
                break
            calls[faction] += 1
            is_error, payload, text = await _call(client, choice.name, choice.arguments)
            if verbose:
                print(f"  t{state['turn']} {faction}: {choice.name} -> {'ERROR ' + text[:90] if is_error else 'ok'}", file=sys.stderr)
            if is_error and choice.name == "submit_action" and "illegal action" in text:
                illegal[faction] += 1
            messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": choice.id or f"call_{calls[faction]}",
                "content": text[:6000], "is_error": is_error, "_tool": choice.name, "_json": payload,
            }]})
            if not is_error and choice.name in ("submit_action", "pass_turn") and payload \
                    and payload.get("faction") == faction:
                acted = True
                state = payload["new_state"]
                state["status"] = payload["status"]
                break
        if not acted:
            stalls[faction] += 1
            is_error, payload, _ = await _call(client, "pass_turn", {"game_id": gid, "faction": faction})
            if is_error:
                _, state, _ = await _call(client, "get_state", {"game_id": gid})
            else:
                state = payload["new_state"]
                state["status"] = payload["status"]
        turns_played = state["turn"] - 1 if state["active_faction"] == "government" else state["turn"]

    if state["status"] == "active":
        await _call(client, "end_game", {"game_id": gid})
    _, final, _ = await _call(client, "get_state", {"game_id": gid})
    _, aar, _ = await _call(client, "after_action_review", {"game_id": gid})
    _, strat, _ = await _call(client, "strategy_profile", {"game_id": gid})
    _, rep, _ = await _call(client, "replay", {"game_id": gid})
    me = "insurgent" if side == "red" else "government"
    gov_score = aar.get("performance_score") if aar else None
    score = gov_score if me == "government" else (round(100 - gov_score, 1) if gov_score is not None else None)
    victory = final.get("victory") or {}
    return GameRow(
        seed=seed, model=providers[me].name, side=me,
        opponent=providers["government" if me == "insurgent" else "insurgent"].name, game_id=gid,
        turns=max(turns_played, 0), winner=victory.get("winner"), condition=victory.get("condition"),
        performance_score=score, aar_government_score=gov_score,
        approach=(strat or {}).get("approach"), illegal_attempts=illegal[me], tool_calls=calls[me],
        stalls=stalls[me], reproduced=(rep or {}).get("reproduced"),
        tokens={"input": providers[me].usage.input_tokens, "output": providers[me].usage.output_tokens},
        seconds=round(time.monotonic() - started, 2),
    )


async def run(server: Any, seeds: list[int], red_spec: str, gov_spec: str, model: str, max_turns: int,
              side: str, out: Path, verbose: bool = False) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    async with Client(server) as client:
        with out.open("a") as fh:
            for seed in seeds:
                red = make_provider(red_spec, model)
                gov = make_provider(gov_spec, model)
                row = await play_game(client, seed, red, gov, max_turns, side, verbose)
                fh.write(json.dumps(row.__dict__) + "\n")
                fh.flush()
                print(f"seed {seed}: {row.turns} turns, winner={row.winner}, score={row.performance_score}, "
                      f"illegal={row.illegal_attempts}, reproduced={row.reproduced}", file=sys.stderr)
    return out


def stdio_server() -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
    env.setdefault("COIN_WARGAME_DB", str(RESULTS_DIR / "bench-games.db"))
    return StdioServerParameters(command=sys.executable, args=["-m", "coin_wargame_mcp.server"], env=env,
                                 cwd=str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="coin-bench", description=__doc__.split("\n\n")[0])
    ap.add_argument("--games", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1, help="first seed; games use seed, seed+1, ...")
    ap.add_argument("--red", default="stub", help="provider for the insurgent seat: stub | anthropic")
    ap.add_argument("--gov", default="stub", help="provider for the government seat: stub | anthropic")
    ap.add_argument("--model", default=DEFAULT_ANTHROPIC_MODEL)
    ap.add_argument("--side", choices=["red", "gov"], default="red", help="which seat the row describes")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    label = args.model if "anthropic" in (args.red, args.gov) else "stub"
    out = args.out or RESULTS_DIR / f"{datetime.now():%Y-%m-%d}-{label}.jsonl"
    seeds = list(range(args.seed, args.seed + args.games))
    asyncio.run(run(stdio_server(), seeds, args.red, args.gov, args.model, args.max_turns, args.side, out, args.verbose))
    print(summarize(out))


if __name__ == "__main__":
    main()
