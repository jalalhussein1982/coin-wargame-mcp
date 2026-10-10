"""A state one move from capital capture ends the game on submit_action.

Engine note: `territorial_seizure` does not relocate forces (StateMutator applies
casualties and then re-derives control from the force ratio in the acting province),
so capture happens when insurgents already inside the capital outnumber the garrison
after one more exchange. The fixture sets that up: 10 government troops, 60 insurgents,
control still 'government'; any insurgent action in the capital flips it.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from mcp import Client


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


def rig_capital(db, gid: str) -> None:
    with sqlite3.connect(db) as c:
        (state_json,) = c.execute("SELECT state_json FROM games WHERE game_id=?", (gid,)).fetchone()
        state = json.loads(state_json)
        for p in state["provinces"]:
            if p["id"] == "nouakchott":
                p["government_forces"] = 10
                p["insurgent_forces"] = 60
        state["global_state"]["active_faction"] = "insurgent"
        c.execute("UPDATE games SET state_json=?, controller_json=? WHERE game_id=?",
                  (json.dumps(state), json.dumps({"phase": "insurgent_action", "session_state": "active",
                                                  "actions_this_turn": []}), gid))


@pytest.mark.anyio
async def test_capital_capture_ends_the_game(client: Client, tmp_path):
    gid = (await client.call_tool("new_game", {"seed": 3})).structured_content["game_id"]
    rig_capital(tmp_path / "games.db", gid)
    ambush = {"action_type": "ambush", "source_province": "nouakchott", "forces_committed": 60}
    r = await client.call_tool("submit_action", {"game_id": gid, "faction": "insurgent", "action": ambush,
                                                  "rationale": "ISIS_TERRITORIAL: take the capital"})
    assert not r.is_error, r.content[0].text
    r = r.structured_content
    assert r["victory"] is not None, r["adjudication"]["outcome"]
    # The engine checks victory after the two phase advances, so an insurgent capture on turn 1
    # is stamped with the turn that has just begun (2). Same as the vendored API's resolve path.
    assert r["victory"] == {"winner": "insurgent", "condition": "Capital Captured - Insurgent forces seized the capital",
                            "turn": 2}
    assert r["status"] == "ended"
    assert any(m["target"] == "province.nouakchott.control" and m["new_value"] == "insurgent" for m in r["mutations"])
    s = (await client.call_tool("get_state", {"game_id": gid})).structured_content
    assert s["status"] == "ended" and s["victory"]["winner"] == "insurgent"
    cards = (await client.call_tool("list_games", {})).structured_content["result"]
    assert cards[0]["winner"] == "insurgent"
    # After victory the game is read-only for actions; the AAR is available without end_game.
    r = await client.call_tool("submit_action", {"game_id": gid, "faction": "government",
                                                  "action": {"action_type": "patrol", "source_province": "trarza", "forces_committed": 10}})
    assert r.is_error and "game is over" in r.content[0].text and "insurgent won" in r.content[0].text
    r = await client.call_tool("pass_turn", {"game_id": gid, "faction": "government"})
    assert r.is_error and "game is over" in r.content[0].text
    a = (await client.call_tool("after_action_review", {"game_id": gid})).structured_content
    # The AAR is scored from the government seat: defeat base 20 plus territory, legitimacy and
    # force-preservation credit, so a one-move loss still lands in the 60s-70s.
    assert a["outcome"] == "defeat" and 0 <= a["performance_score"] <= 100
    assert any("capital" in tp.get("impact", "").lower() or "nouakchott" in tp.get("impact", "").lower()
               for tp in a["turning_points"]) or a["turning_points"] == []
    # The rigged row cannot replay from the seed, and the tool says so instead of hiding it.
    assert (await client.call_tool("replay", {"game_id": gid})).structured_content["reproduced"] is False
