"""new_game / list_games / end_game / replay on a fresh game."""

from __future__ import annotations

import pytest

from mcp import Client


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_new_game_is_deterministic_for_a_seed(client: Client):
    a = (await client.call_tool("new_game", {"seed": 42})).structured_content
    b = (await client.call_tool("new_game", {"seed": 42})).structured_content
    assert a["game_id"] != b["game_id"]
    assert a["seed"] == b["seed"] == 42
    sa, sb = a["initial_state"], b["initial_state"]
    for k in ("turn", "phase", "active_faction", "global_state", "provinces"):
        assert sa[k] == sb[k], k
    assert sa["turn"] == 1 and sa["phase"] == "government_action" and sa["active_faction"] == "government"
    assert len(sa["provinces"]) == 13
    assert sa["version"] == 1 and sa["status"] == "active"


@pytest.mark.anyio
async def test_seat_tokens_only_for_open_seats(client: Client):
    both_host = (await client.call_tool("new_game", {"seed": 1})).structured_content
    assert both_host["seats"] == {"government": "host", "insurgent": "host"}
    assert both_host["seat_tokens"] == {"government": None, "insurgent": None}
    one_open = (await client.call_tool("new_game", {"seed": 1, "seats": {"insurgent": "open"}})).structured_content
    assert one_open["seats"] == {"government": "host", "insurgent": "open"}
    assert one_open["seat_tokens"]["government"] is None
    tok = one_open["seat_tokens"]["insurgent"]
    assert isinstance(tok, str) and len(tok) == 32


@pytest.mark.anyio
async def test_new_game_rejects_unknown_seat_and_scenario(client: Client):
    r = await client.call_tool("new_game", {"seats": {"referee": "open"}})
    assert r.is_error and "referee" in r.content[0].text
    r = await client.call_tool("new_game", {"scenario": "mars"})
    assert r.is_error  # input validation: Literal


@pytest.mark.anyio
async def test_end_game_and_list_games(client: Client):
    g = (await client.call_tool("new_game", {"seed": 5})).structured_content
    cards = (await client.call_tool("list_games", {})).structured_content["result"]
    assert [c["game_id"] for c in cards] == [g["game_id"]]
    assert cards[0]["status"] == "active" and cards[0]["turn"] == 1

    s = (await client.call_tool("end_game", {"game_id": g["game_id"]})).structured_content
    assert s["status"] == "ended" and s["total_turns"] == 1 and s["victory"] is None
    assert s["final_state"]["government_provinces"] == 11 and s["final_state"]["contested_provinces"] == 2
    again = (await client.call_tool("end_game", {"game_id": g["game_id"]})).structured_content
    assert again == s  # idempotent

    ended = (await client.call_tool("list_games", {"status": "ended"})).structured_content["result"]
    assert [c["game_id"] for c in ended] == [g["game_id"]]
    assert (await client.call_tool("list_games", {"status": "active"})).structured_content["result"] == []
    r = await client.call_tool("submit_action", {"game_id": g["game_id"], "faction": "government",
                                                  "action": {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 100}})
    assert r.is_error and "game is over" in r.content[0].text


@pytest.mark.anyio
async def test_unknown_game_names_list_games(client: Client):
    r = await client.call_tool("get_state", {"game_id": "nope"})
    assert r.is_error and "list_games" in r.content[0].text


@pytest.mark.anyio
async def test_replay_of_a_fresh_game_is_reproduced(client: Client):
    g = (await client.call_tool("new_game", {"seed": 9})).structured_content
    r = (await client.call_tool("replay", {"game_id": g["game_id"]})).structured_content
    assert r == {"game_id": g["game_id"], "seed": 9, "actions": 0, "reproduced": True,
                 "divergence_at_turn": None, "final_turn": 1}
