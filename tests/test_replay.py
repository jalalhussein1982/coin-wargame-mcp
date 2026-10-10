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
