"""list_legal_actions never returns an action the validator rejects (property over seeds and play)."""

from __future__ import annotations

import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from coin_wargame_mcp import _engine as E
from coin_wargame_mcp.engine import Engine, make_action
from mcp import Client


def _play_random(eng: Engine, rng: random.Random, steps: int) -> None:
    for _ in range(steps):
        faction = eng.active_faction
        acts = eng.legal_actions(faction)
        if not acts:
            eng.pass_turn(faction)
            continue
        a = rng.choice(acts)
        eng.submit(faction, make_action(faction, a), "random play")
        if eng.controller["session_state"] != "active":
            return


@settings(max_examples=24, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 20), steps=st.integers(0, 6))
def test_every_legal_action_validates(seed: int, steps: int):
    eng = Engine.new(seed)
    _play_random(eng, random.Random(seed), steps)
    if eng.controller["session_state"] != "active":
        return
    for faction in ("government", "insurgent"):
        acts = eng.legal_actions(faction)
        assert acts, faction
        for a in acts:
            v = E.rules_engine.validate_action(make_action(faction, a), eng.gs)
            assert v.is_valid, (faction, a, v.errors)
            assert a["min_forces"] == E.rules_engine.MIN_FORCE_REQUIREMENTS.get(E.ActionType(a["action_type"]), 0)


def test_initial_counts_and_province_filter():
    eng = Engine.new(1)
    gov = eng.legal_actions("government")
    ins = eng.legal_actions("insurgent")
    assert len(gov) > 100 and len(ins) > 50
    # The insurgent has no forces in the capital: force-requiring actions are absent there,
    # and the ones that remain cost nothing in forces.
    capital = eng.legal_actions("insurgent", "nouakchott")
    assert all(a["min_forces"] == 0 for a in capital)
    assert not any(a["action_type"] == "ambush" for a in capital)
    # Adjacency-typed actions only carry adjacent targets.
    for a in gov:
        if a["target_province"]:
            assert a["target_province"] in E.rules_engine.PROVINCE_ADJACENCY[a["source_province"]]


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_tool_reports_turn_ownership_and_truncation(client: Client):
    g = (await client.call_tool("new_game", {"seed": 3})).structured_content
    r = (await client.call_tool("list_legal_actions", {"game_id": g["game_id"], "faction": "government", "limit": 5})).structured_content
    assert r["your_turn"] is True and r["truncated"] is True and len(r["actions"]) == 5 and r["total"] > 5
    r = (await client.call_tool("list_legal_actions", {"game_id": g["game_id"], "faction": "insurgent"})).structured_content
    assert r["your_turn"] is False
    r = await client.call_tool("list_legal_actions", {"game_id": g["game_id"], "faction": "insurgent", "province": "atlantis"})
    assert r.is_error and "atlantis" in r.content[0].text


@pytest.mark.anyio
async def test_finished_game_is_a_tool_error_not_an_empty_list(client: Client):
    g = (await client.call_tool("new_game", {"seed": 3})).structured_content
    await client.call_tool("end_game", {"game_id": g["game_id"]})
    r = await client.call_tool("list_legal_actions", {"game_id": g["game_id"], "faction": "government"})
    assert r.is_error
    assert "game is over" in r.content[0].text
