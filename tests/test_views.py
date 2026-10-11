"""State views (fog rule), province detail, briefs, preview and doctrine advice."""

from __future__ import annotations

import json

import pytest

from mcp import Client


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.fixture
async def game(client: Client) -> str:
    return (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]


@pytest.mark.anyio
async def test_government_view_hides_true_insurgent_numbers(client: Client, game: str):
    gov = (await client.call_tool("get_state", {"game_id": game, "view": "government"})).structured_content
    for p in gov["provinces"]:
        assert "insurgent_forces" not in p, p["id"]
        assert "insurgent_estimated" in p
    assert gov["global_state"]["insurgent_resources"] is None
    assert gov["global_state"]["faction_forces"]["insurgent_total"] is None
    # The estimate is the server's lagged, noisy model (seeded, see engine.py), not the true 3500
    # and not the true share of the engine's frozen 2500.
    est_total = sum(p["insurgent_estimated"] for p in gov["provinces"])
    assert gov["global_state"]["faction_forces"]["insurgent_estimated_by_government"] == est_total
    assert 1800 <= est_total <= 3200 and est_total != 3500

    ins = (await client.call_tool("get_state", {"game_id": game, "view": "insurgent"})).structured_content
    by_id = {p["id"]: p for p in ins["provinces"]}
    assert by_id["hodh_ech_chargui"]["insurgent_forces"] == 1200
    assert ins["global_state"]["insurgent_resources"] == 30
    full = (await client.call_tool("get_state", {"game_id": game})).structured_content
    assert full["provinces"] == ins["provinces"] and full["view"] == "full"


@pytest.mark.anyio
async def test_province_detail_and_resource_match(client: Client, game: str):
    d = (await client.call_tool("get_province", {"game_id": game, "province_id": "hodh_ech_chargui"})).structured_content
    assert d["province"]["borders_mali"] is True and d["province"]["control"] == "contested"
    assert d["text"].startswith("Province: Hodh Ech Chargui (hodh_ech_chargui)")
    assert d["legal_action_count"]["government"] > 0 and d["legal_action_count"]["insurgent"] > 0
    r = await client.call_tool("get_province", {"game_id": game, "province_id": "atlantis"})
    assert r.is_error and "atlantis" in r.content[0].text
    res = await client.read_resource(f"wargame://game/{game}")
    doc = json.loads(res.contents[0].text)
    assert doc["game_id"] == game and len(doc["provinces"]) == 13


@pytest.mark.anyio
async def test_briefs_for_both_seats(client: Client, game: str):
    ins = (await client.call_tool("situation_brief", {"game_id": game, "faction": "insurgent"})).structured_content
    assert ins["markdown"].startswith("# STRATEGIC SITUATION ASSESSMENT - TURN 1")
    assert ins["recommended_doctrine"] in {"AQ_NETWORK", "ISIS_TERRITORIAL", "TALIBAN_ADAPTIVE"}
    assert ins["strategic_opportunities"] and any("BORDER EXPLOIT" in o for o in ins["strategic_opportunities"])
    gov = (await client.call_tool("situation_brief", {"game_id": game, "faction": "government"})).structured_content
    assert gov["markdown"].startswith("# STRATEGIC SITUATION ASSESSMENT - TURN 1")
    for section in ("## FORCE SUMMARY", "## TERRITORIAL CONTROL", "## MALI BORDER CORRIDOR", "## POPULATION SENTIMENT",
                    "## RESOURCE STATUS", "## SCENARIO CONDITIONS", "## INSURGENT PATTERNS", "## STRATEGIC OPPORTUNITIES"):
        assert section in gov["markdown"], section
    # The true counts (1200 in Hodh Ech Chargui, 800 in Hodh El Gharbi) never reach the government,
    # and neither does the true share of the engine's 2500 (857 / 571), which was the earlier leak.
    for leak in ("ins=1200", "Ins=1200", "ins=800", "Ins=800", "~857", "~571"):
        assert leak not in gov["markdown"], leak
    assert "ins~" in gov["markdown"] and "Ins~" in gov["markdown"]
    assert gov["recommended_doctrine"] is None


@pytest.mark.anyio
async def test_preview_returns_a_bounded_probability_and_validation(client: Client, game: str):
    action = {"action_type": "assault", "source_province": "hodh_ech_chargui", "forces_committed": 1000}
    p = (await client.call_tool("preview_action", {"game_id": game, "faction": "government", "action": action})).structured_content
    assert 5 <= p["final_probability"] <= 95
    assert p["base_probability"] + p["modifier_total"] >= p["final_probability"] - 0 or p["final_probability"] in (5, 95)
    assert abs(sum(p["outcome_probabilities"].values()) - 100) < 1e-6
    assert p["validation"]["is_valid"] is True
    assert isinstance(p["modifier_explanations"], list)
    bad = {"action_type": "assault", "source_province": "nouakchott", "forces_committed": 100}
    p = (await client.call_tool("preview_action", {"game_id": game, "faction": "government", "action": bad})).structured_content
    assert p["validation"]["is_valid"] is False and any("minimum 500" in e for e in p["validation"]["errors"])
    r = await client.call_tool("preview_action", {"game_id": game, "faction": "insurgent",
                                                   "action": {"action_type": "teleport", "source_province": "adrar"}})
    assert r.is_error and "teleport" in r.content[0].text and "ambush" in r.content[0].text


@pytest.mark.anyio
async def test_recommend_doctrine_names_a_resource(client: Client, game: str):
    d = (await client.call_tool("recommend_doctrine", {"game_id": game})).structured_content
    assert d["doctrine"] in {"AQ_NETWORK", "ISIS_TERRITORIAL", "TALIBAN_ADAPTIVE"}
    assert d["description_uri"] == f"wargame://doctrine/{d['doctrine'].lower()}"
    text = (await client.read_resource(d["description_uri"])).contents[0].text
    assert text.startswith(f"# {d['doctrine']} Doctrine")
