"""Every QA pair in evals/coin-wargame.xml holds against a live replay of the seed-7 game."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mcp import Client
from tests import scripted

EVALS = Path(__file__).resolve().parent.parent / "evals" / "coin-wargame.xml"


def _get(doc, path: str):
    cur = doc
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            cur = cur[part]
    return cur


def _expected(s: str):
    """Numbers, booleans and null are JSON; anything else is a bare string."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


@pytest.mark.anyio
async def test_eval_pairs_hold():
    from coin_wargame_mcp.server import mcp

    root = ET.parse(EVALS).getroot()
    qas = root.findall("qa")
    assert len(qas) == 10
    async with Client(mcp, raise_exceptions=True) as c:
        gid = (await c.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
        await scripted.play(c, gid, turns=10)
        docs = {
            "end_game": (await c.call_tool("end_game", {"game_id": gid})).structured_content,
            "log": json.loads((await c.read_resource(f"wargame://game/{gid}/log")).contents[0].text),
            "replay": (await c.call_tool("replay", {"game_id": gid})).structured_content,
            "state": (await c.call_tool("get_state", {"game_id": gid})).structured_content,
            "strategy": (await c.call_tool("strategy_profile", {"game_id": gid})).structured_content,
            "patterns": (await c.call_tool("pattern_report", {"game_id": gid})).structured_content,
            "aar": (await c.call_tool("after_action_review", {"game_id": gid})).structured_content,
        }
    source = {"1": "log", "2": "log", "3": "replay", "4": "end_game", "5": "end_game", "6": "state",
              "7": "strategy", "8": "patterns", "9": "aar", "10": "state"}
    for qa in qas:
        doc = docs[source[qa.get("id")]]
        for chk in qa.findall("check"):
            got = _get(doc, chk.get("path"))
            exp = _expected(chk.get("expected"))
            assert got == exp, (qa.get("id"), chk.get("path"), got, exp)
