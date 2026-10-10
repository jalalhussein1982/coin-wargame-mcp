"""Learning tools over the server's flattened log."""

from __future__ import annotations

import pytest

from mcp import Client
from tests import scripted


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.fixture
async def played(client: Client) -> str:
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    await scripted.play(client, gid, turns=10)
    return gid


@pytest.mark.anyio
async def test_strategy_profile_sees_the_government_actions(client: Client, played: str):
    s = (await client.call_tool("strategy_profile", {"game_id": played})).structured_content
    assert s["government_actions_seen"] == 10  # the flattened-faction fix
    assert s["approach"] in {"population_centric", "enemy_centric", "clear_hold_build", "ink_spot", "attrition", "hybrid"}
    assert 0.0 <= s["confidence"] <= 1.0 and s["approach_scores"]
    assert s["evidence"]


@pytest.mark.anyio
async def test_strategy_profile_on_an_unplayed_game_is_undefined_not_an_error(client: Client):
    gid = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    s = (await client.call_tool("strategy_profile", {"game_id": gid})).structured_content
    assert s["approach"] == "undefined" and s["government_actions_seen"] == 0


@pytest.mark.anyio
async def test_after_action_review_requires_an_ended_game(client: Client, played: str):
    r = await client.call_tool("after_action_review", {"game_id": played})
    assert r.is_error and "end_game" in r.content[0].text
    await client.call_tool("end_game", {"game_id": played})
    a = (await client.call_tool("after_action_review", {"game_id": played})).structured_content
    assert 0 <= a["performance_score"] <= 100
    assert a["outcome"] == "stalemate"
    assert a["executive_summary"] and a["lessons_learned"]
    assert "population_centric_ratio" in a["doctrine_compliance"]
    assert isinstance(a["turning_points"], list)


@pytest.mark.anyio
async def test_turn_feedback_defaults_to_latest_government_action(client: Client, played: str):
    f = (await client.call_tool("turn_feedback", {"game_id": played})).structured_content
    assert f["turn"] == 10 and f["action"]["action_type"] == "media_campaign"
    assert f["key_insight"] and f["historical_parallel"] and f["red_team_prediction"]
    f3 = (await client.call_tool("turn_feedback", {"game_id": played, "turn": 3})).structured_content
    assert f3["turn"] == 3 and f3["action"]["action_type"] == "intelligence_operation"
    r = await client.call_tool("turn_feedback", {"game_id": played, "turn": 99})
    assert r.is_error and "turn 99" in r.content[0].text
    fresh = (await client.call_tool("new_game", {"seed": 1})).structured_content["game_id"]
    r = await client.call_tool("turn_feedback", {"game_id": fresh})
    assert r.is_error and "no government action" in r.content[0].text


@pytest.mark.anyio
async def test_pattern_report_reads_the_whole_log(client: Client, played: str):
    p = (await client.call_tool("pattern_report", {"game_id": played})).structured_content
    assert p["favorite_actions"][0] == "patrol"
    assert 0.0 <= p["predictability_score"] <= 1.0
    assert p["geographic_focus"] and p["risk_profile"] and p["tendencies"] is not None
