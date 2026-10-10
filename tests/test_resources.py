"""Resources with cache hints, and the three prompts."""

from __future__ import annotations

import json

import pytest

from mcp import Client


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_doctrines_are_public_and_cached_for_an_hour(client: Client):
    for name in ("aq_network", "isis_territorial", "taliban_adaptive"):
        res = await client.read_resource(f"wargame://doctrine/{name}")
        assert res.contents[0].text.startswith(f"# {name.upper()} Doctrine")
        assert res.ttl_ms == 3_600_000 and res.cache_scope == "public"
    with pytest.raises(Exception):
        await client.read_resource("wargame://doctrine/../README")


@pytest.mark.anyio
async def test_rules_resources_come_from_engine_constants(client: Client):
    actions = json.loads((await client.read_resource("wargame://rules/actions")).contents[0].text)
    assert actions["actions"]["assault"] == {"factions": ["government"], "cost": 15, "min_forces": 500,
                                             "target_must_be_adjacent": True, "requires_source_control": False}
    assert actions["actions"]["reinforce"]["factions"] == ["government", "insurgent"]
    assert len(actions["actions"]) == 36 and actions["rules"]
    tables = json.loads((await client.read_resource("wargame://rules/probability-tables")).contents[0].text)
    assert "base_success_rates" in tables and "critical_thresholds" in tables
    m = json.loads((await client.read_resource("wargame://rules/map")).contents[0].text)
    assert m["provinces"]["nouakchott"]["is_capital"] is True
    assert m["adjacency"]["nouakchott"] == ["trarza", "inchiri"] and len(m["provinces"]) == 13


@pytest.mark.anyio
async def test_game_resources_are_private_and_never_fresh(client: Client):
    gid = (await client.call_tool("new_game", {"seed": 2})).structured_content["game_id"]
    res = await client.read_resource(f"wargame://game/{gid}")
    assert res.ttl_ms == 0 and res.cache_scope == "private"
    assert json.loads(res.contents[0].text)["view"] == "full"
    res = await client.read_resource(f"wargame://game/{gid}/log")
    assert json.loads(res.contents[0].text) == {"game_id": gid, "seed": 2, "status": "active", "version": 1, "log": []}
    with pytest.raises(Exception):
        await client.read_resource("wargame://game/missing")


@pytest.mark.anyio
async def test_prompts_render_tool_instructions(client: Client):
    p = await client.get_prompt("play_red_team", {"game_id": "g1"})
    text = p.messages[0].content.text
    assert "g1" in text
    for tool in ("list_legal_actions", "preview_action", "submit_action", "recommend_doctrine"):
        assert tool in text, tool
    assert "## IDENTITY" in text and "OUTPUT FORMAT" not in text  # the JSON contract became the tool schema
    p = await client.get_prompt("play_red_team", {"game_id": "g1", "doctrine": "isis_territorial"})
    assert "wargame://doctrine/isis_territorial" in p.messages[0].content.text
    p = await client.get_prompt("play_government", {"game_id": "g1", "approach": "ink_spot"})
    assert "ink_spot" in p.messages[0].content.text and "request_international_community" in p.messages[0].content.text
    p = await client.get_prompt("after_action_debrief", {"game_id": "g1"})
    for tool in ("after_action_review", "strategy_profile", "pattern_report", "replay"):
        assert tool in p.messages[0].content.text
