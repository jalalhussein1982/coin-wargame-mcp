"""submit_action recipe, illegal actions, phase guards, pass_turn."""

from __future__ import annotations

import pytest

from mcp import Client

PATROL = {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 500}


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.fixture
async def game(client: Client) -> str:
    return (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]


@pytest.mark.anyio
async def test_legal_government_action_is_adjudicated_and_advances_the_phase(client: Client, game: str):
    r = (await client.call_tool("submit_action", {"game_id": game, "faction": "government", "action": PATROL,
                                                   "rationale": "show presence in the capital"})).structured_content
    adj = r["adjudication"]
    assert adj["outcome"] in {"critical_success", "success", "partial_success", "failure", "critical_failure"}
    assert 1 <= adj["success_roll"] <= 100 and 5 <= adj["required_roll"] <= 95
    assert adj["action"]["action_type"] == "patrol" and adj["narrative"]
    assert any(m["target"] == "global.government_resources" and m["new_value"] == 97 for m in r["mutations"])
    assert r["turn_played"] == 1 and r["status"] == "active" and r["victory"] is None
    assert r["next"] == {"turn": 1, "active_faction": "insurgent", "phase": "insurgent_action"}
    assert r["version"] == 2
    assert r["new_state"]["global_state"]["government_resources"] == 97
    s = (await client.call_tool("get_state", {"game_id": game})).structured_content
    assert s["version"] == 2 and s["phase"] == "insurgent_action"
    log = (await client.read_resource(f"wargame://game/{game}/log")).contents[0].text
    assert "show presence in the capital" in log


@pytest.mark.anyio
async def test_illegal_action_is_refused_with_errors_and_alternatives_and_leaves_state(client: Client, game: str):
    too_many = {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 99999}
    r = await client.call_tool("submit_action", {"game_id": game, "faction": "government", "action": too_many})
    assert r.is_error
    text = r.content[0].text
    assert "Insufficient forces: 99999 committed but only 5000 available in nouakchott" in text
    assert "Legal alternatives:" in text and "from nouakchott" in text
    s = (await client.call_tool("get_state", {"game_id": game})).structured_content
    assert s["version"] == 1 and s["phase"] == "government_action"
    assert s["global_state"]["government_resources"] == 100


@pytest.mark.anyio
async def test_acting_out_of_phase_is_refused(client: Client, game: str):
    ambush = {"action_type": "ambush", "source_province": "hodh_ech_chargui", "forces_committed": 100}
    r = await client.call_tool("submit_action", {"game_id": game, "faction": "insurgent", "action": ambush})
    assert r.is_error and "government's turn" in r.content[0].text and "insurgent_action" in r.content[0].text
    r = await client.call_tool("pass_turn", {"game_id": game, "faction": "insurgent"})
    assert r.is_error and "government's turn" in r.content[0].text
    # Wrong faction for the action type is a validation error, not a crash.
    r = await client.call_tool("submit_action", {"game_id": game, "faction": "government", "action": ambush})
    assert r.is_error and "not available to government" in r.content[0].text


@pytest.mark.anyio
async def test_pass_turn_advances_phases_and_turns(client: Client, game: str):
    r = (await client.call_tool("pass_turn", {"game_id": game, "faction": "government"})).structured_content
    assert r["adjudication"] is None and r["mutations"] == []
    assert r["next"] == {"turn": 1, "active_faction": "insurgent", "phase": "insurgent_action"}
    r = (await client.call_tool("pass_turn", {"game_id": game, "faction": "insurgent"})).structured_content
    assert r["next"] == {"turn": 2, "active_faction": "government", "phase": "government_action"}
    assert r["version"] == 3
    s = (await client.call_tool("get_state", {"game_id": game})).structured_content
    assert s["turn"] == 2


@pytest.mark.anyio
async def test_full_turn_both_seats_and_unknown_inputs(client: Client, game: str):
    await client.call_tool("submit_action", {"game_id": game, "faction": "government", "action": PATROL})
    r = (await client.call_tool("submit_action", {
        "game_id": game, "faction": "insurgent",
        "action": {"action_type": "propaganda", "source_province": "hodh_ech_chargui", "forces_committed": 0},
        "rationale": "AQ_NETWORK: erode legitimacy cheaply"})).structured_content
    assert r["next"]["turn"] == 2 and r["next"]["active_faction"] == "government"
    assert r["new_state"]["global_state"]["insurgent_resources"] == 26
    r = await client.call_tool("submit_action", {"game_id": game, "faction": "government",
                                                  "action": {"action_type": "patrol", "source_province": "atlantis"}})
    assert r.is_error and "atlantis" in r.content[0].text
