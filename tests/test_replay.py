"""A scripted ten-turn game replays exactly; a tampered record does not."""

from __future__ import annotations

import json
import sqlite3

import pytest

from mcp import Client
from tests import scripted


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_ten_turn_seed_7_game_is_reproduced(client: Client):
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    results = await scripted.play(client, gid, turns=10)
    assert len(results) == 20 and results[-1]["next"]["turn"] == 11
    r = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert r["reproduced"] is True
    assert r["divergence_at_turn"] is None
    assert r["actions"] == 21  # 20 actions + 1 IC request
    assert r["final_turn"] == 11


@pytest.mark.anyio
async def test_same_seed_and_script_give_identical_games(client: Client):
    a = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    b = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    ra = await scripted.play(client, a, turns=4)
    rb = await scripted.play(client, b, turns=4)
    for x, y in zip(ra, rb):
        assert x["adjudication"]["success_roll"] == y["adjudication"]["success_roll"]
        assert x["adjudication"]["outcome"] == y["adjudication"]["outcome"]
    sa = (await client.call_tool("get_state", {"game_id": a})).structured_content
    sb = (await client.call_tool("get_state", {"game_id": b})).structured_content
    assert sa["provinces"] == sb["provinces"] and sa["global_state"] == sb["global_state"]
    # The IC decision on turn 3 is part of what must agree.
    la = json.loads((await client.read_resource(f"wargame://game/{a}/log")).contents[0].text)["log"]
    lb = json.loads((await client.read_resource(f"wargame://game/{b}/log")).contents[0].text)["log"]
    ica = next(e for e in la if e["kind"] == "ic_request")
    icb = next(e for e in lb if e["kind"] == "ic_request")
    assert ica["ic"]["decision"] == icb["ic"]["decision"]


@pytest.mark.anyio
async def test_tampered_state_is_reported_not_hidden(client: Client, tmp_path):
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    await scripted.play(client, gid, turns=3)
    with sqlite3.connect(tmp_path / "games.db") as c:
        (state_json,) = c.execute("SELECT state_json FROM games WHERE game_id=?", (gid,)).fetchone()
        state = json.loads(state_json)
        state["provinces"][0]["government_forces"] += 1  # someone edited the row
        c.execute("UPDATE games SET state_json=? WHERE game_id=?", (json.dumps(state), gid))
    r = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert r["reproduced"] is False
    assert r["divergence_at_turn"] == 4  # the state claims turn 4; the re-run disagrees there


@pytest.mark.anyio
async def test_tampered_dice_state_and_controller_are_detected(client: Client, tmp_path):
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    await scripted.play(client, gid, turns=3)
    db = tmp_path / "games.db"
    with sqlite3.connect(db) as c:
        (dice_json, ctrl_json) = c.execute("SELECT dice_state, controller_json FROM games WHERE game_id=?", (gid,)).fetchone()
        dice = json.loads(dice_json)
        dice[1][0] = (dice[1][0] + 1) % (2**32)
        c.execute("UPDATE games SET dice_state=? WHERE game_id=?", (json.dumps(dice), gid))
    r = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert r["reproduced"] is False, "a corrupted dice state must not report reproduced"
    with sqlite3.connect(db) as c:
        c.execute("UPDATE games SET dice_state=? WHERE game_id=?", (dice_json, gid))
        ctrl = json.loads(ctrl_json)
        ctrl["estimates"]["nouakchott"] = 999
        c.execute("UPDATE games SET controller_json=? WHERE game_id=?", (json.dumps(ctrl), gid))
    r = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert r["reproduced"] is False


@pytest.mark.anyio
async def test_divergence_turn_is_the_first_entry_whose_recorded_state_hash_disagrees(client: Client, tmp_path):
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    await scripted.play(client, gid, turns=5)
    with sqlite3.connect(tmp_path / "games.db") as c:
        (log_json,) = c.execute("SELECT turn_log_json FROM games WHERE game_id=?", (gid,)).fetchone()
        log = json.loads(log_json)
        assert all("state_hash" in e for e in log if e["kind"] in ("action", "pass", "ic_request"))
        # Pretend turn 3's government action was something else: the record no longer matches.
        gov3 = next(e for e in log if e["kind"] == "action" and e["turn"] == 3 and e["faction"] == "government")
        gov3["action"]["source_province"] = "gorgol"
        c.execute("UPDATE games SET turn_log_json=? WHERE game_id=?", (json.dumps(log), gid))
    r = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert r["reproduced"] is False and r["divergence_at_turn"] == 3
