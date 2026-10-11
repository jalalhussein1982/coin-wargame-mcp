"""Open seats need their token; host seats do not."""

from __future__ import annotations

import pytest

from mcp import Client

PATROL = {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 500}
PROPAGANDA = {"action_type": "propaganda", "source_province": "hodh_ech_chargui"}


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_open_seat_refuses_without_token_and_accepts_with_it(client: Client):
    g = (await client.call_tool("new_game", {"seed": 7, "seats": {"insurgent": "open"}})).structured_content
    gid, tok = g["game_id"], g["seat_tokens"]["insurgent"]
    await client.call_tool("submit_action", {"game_id": gid, "faction": "government", "action": PATROL})  # host seat
    r = await client.call_tool("submit_action", {"game_id": gid, "faction": "insurgent", "action": PROPAGANDA})
    assert r.is_error and "the insurgent seat is open; present its seat_token" in r.content[0].text
    r = await client.call_tool("submit_action", {"game_id": gid, "faction": "insurgent", "action": PROPAGANDA,
                                                  "seat_token": "0" * 32})
    assert r.is_error and "insurgent seat is open" in r.content[0].text
    r = await client.call_tool("pass_turn", {"game_id": gid, "faction": "insurgent"})
    assert r.is_error and "insurgent seat is open" in r.content[0].text
    s = (await client.call_tool("get_state", {"game_id": gid})).structured_content
    assert s["version"] == 2  # none of the refused calls wrote anything
    r = await client.call_tool("submit_action", {"game_id": gid, "faction": "insurgent", "action": PROPAGANDA,
                                                  "seat_token": tok})
    assert not r.is_error and r.structured_content["next"]["turn"] == 2


@pytest.mark.anyio
async def test_open_government_seat_guards_ic_requests_too(client: Client):
    g = (await client.call_tool("new_game", {"seed": 7, "seats": {"government": "open"}})).structured_content
    gid, tok = g["game_id"], g["seat_tokens"]["government"]
    args = {"game_id": gid, "request_type": "humanitarian", "justification": "displacement in the east"}
    r = await client.call_tool("request_international_community", args)
    assert r.is_error and "government seat is open" in r.content[0].text
    r = await client.call_tool("request_international_community", {**args, "seat_token": tok})
    assert not r.is_error
    assert r.structured_content["decision"] in {"approved", "conditional", "delayed", "denied"}
    assert r.structured_content["version"] == 2


@pytest.mark.anyio
async def test_end_game_needs_a_token_when_a_seat_is_open(client: Client):
    g = (await client.call_tool("new_game", {"seed": 7, "seats": {"insurgent": "open"}})).structured_content
    gid, tok = g["game_id"], g["seat_tokens"]["insurgent"]
    r = await client.call_tool("end_game", {"game_id": gid})
    assert r.is_error and "insurgent seat is open" in r.content[0].text
    r = await client.call_tool("end_game", {"game_id": gid, "seat_token": "0" * 32})
    assert r.is_error
    assert (await client.call_tool("get_state", {"game_id": gid})).structured_content["status"] == "active"
    r = (await client.call_tool("end_game", {"game_id": gid, "seat_token": tok})).structured_content
    assert r["status"] == "ended"
    # Once ended, the log and the seed are readable by anyone; end_game stays idempotent without a token.
    assert (await client.call_tool("end_game", {"game_id": gid})).structured_content["status"] == "ended"
    assert (await client.call_tool("replay", {"game_id": gid})).structured_content["seed"] == 7
    await client.read_resource(f"wargame://game/{gid}/log")
