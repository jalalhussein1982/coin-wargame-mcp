"""Fog of war is enforced on every government-facing payload, not only on get_state(view=government).

With the insurgent seat open, the creating host plays the government and must never see
`insurgent_forces`, `insurgent_resources`, the true `insurgent_total`, or the dice seed.
"""

from __future__ import annotations

import json

import pytest

from mcp import Client

SECRET_KEYS = {"insurgent_forces", "insurgent_resources"}
PATROL = {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 500}


def _keys(obj, found=None):
    found = set() if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, found)
    return found


def _nonnull_secrets(obj) -> set[str]:
    """Secret keys present with a non-null value anywhere in the payload."""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in SECRET_KEYS and v is not None:
                    out.add(k)
                if k == "insurgent_total" and v is not None:
                    out.add(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return out


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_government_host_never_sees_true_insurgent_numbers_or_the_seed(client: Client):
    g = (await client.call_tool("new_game", {"seats": {"insurgent": "open"}})).structured_content
    gid, tok = g["game_id"], g["seat_tokens"]["insurgent"]
    assert g["seed"] is None, "a server-drawn seed is not shown to a host that does not hold both seats"
    assert g["initial_state"]["view"] == "government" and g["initial_state"]["seed"] is None
    assert not _nonnull_secrets(g["initial_state"])

    r = (await client.call_tool("submit_action", {"game_id": gid, "faction": "government", "action": PATROL})).structured_content
    assert r["new_state"]["view"] == "government"
    assert not _nonnull_secrets(r["new_state"]), _nonnull_secrets(r["new_state"])
    # The adjudication's mutations describe the government's own action only; still no secret keys.
    assert not _nonnull_secrets(r["adjudication"]) and not _nonnull_secrets(r["mutations"])

    await client.call_tool("submit_action", {"game_id": gid, "faction": "insurgent", "seat_token": tok,
                                             "action": {"action_type": "propaganda", "source_province": "hodh_ech_chargui"}})
    r = (await client.call_tool("pass_turn", {"game_id": gid, "faction": "government"})).structured_content
    assert r["new_state"]["view"] == "government" and not _nonnull_secrets(r["new_state"])

    # Reads default to what the caller may see.
    s = (await client.call_tool("get_state", {"game_id": gid})).structured_content
    assert s["view"] == "government" and s["seed"] is None and not _nonnull_secrets(s)
    for view in ("full", "insurgent"):
        r = await client.call_tool("get_state", {"game_id": gid, "view": view})
        assert r.is_error and "insurgent seat is open" in r.content[0].text
    s = (await client.call_tool("get_state", {"game_id": gid, "view": "insurgent", "seat_token": tok})).structured_content
    assert s["view"] == "insurgent" and "insurgent_forces" in s["provinces"][0]

    p = (await client.call_tool("get_province", {"game_id": gid, "province_id": "hodh_ech_chargui"})).structured_content
    assert "insurgent_forces" not in p["province"] and "insurgent_estimated" in p["province"]
    assert "Insurgent Forces: 1200" not in p["text"] and "~" in p["text"]
    assert list(p["legal_action_count"]) == ["government"]

    cards = (await client.call_tool("list_games", {})).structured_content["result"]
    assert cards == [], "games with an open seat are hidden from callers without a token"
    cards = (await client.call_tool("list_games", {"seat_token": tok})).structured_content["result"]
    assert [c["game_id"] for c in cards] == [gid] and cards[0]["seed"] is None

    rep = (await client.call_tool("replay", {"game_id": gid})).structured_content
    assert rep["reproduced"] is True and rep["seed"] is None

    doc = json.loads((await client.read_resource(f"wargame://game/{gid}")).contents[0].text)
    assert doc["view"] == "government" and doc["seed"] is None and not _nonnull_secrets(doc)
    with pytest.raises(Exception):
        await client.read_resource(f"wargame://game/{gid}/log")

    # Insurgent-only reads need the token while the seat is open.
    for name, args in (("situation_brief", {"faction": "insurgent"}), ("recommend_doctrine", {}),
                       ("list_legal_actions", {"faction": "insurgent"})):
        r = await client.call_tool(name, {"game_id": gid, **args})
        assert r.is_error and "insurgent seat is open" in r.content[0].text, name
        r = await client.call_tool(name, {"game_id": gid, "seat_token": tok, **args})
        assert not r.is_error, name
    # Government reads do not.
    b = (await client.call_tool("situation_brief", {"game_id": gid, "faction": "government"})).structured_content
    assert "ins=" not in b["markdown"]


@pytest.mark.anyio
async def test_government_preview_is_computed_against_the_estimate(client: Client):
    g = (await client.call_tool("new_game", {"seed": 7, "seats": {"insurgent": "open"}})).structured_content
    gid = g["game_id"]
    assault = {"action_type": "assault", "source_province": "tagant", "target_province": "hodh_ech_chargui",
               "forces_committed": 900}
    p = (await client.call_tool("preview_action", {"game_id": gid, "faction": "government", "action": assault})).structured_content
    text = json.dumps(p)
    assert "900:1200" not in text, "the true defender count must not appear in a government preview"
    est = next(x["insurgent_estimated"] for x in g["initial_state"]["provinces"] if x["id"] == "hodh_ech_chargui")
    assert f"900:{est}" in text
    # The insurgent's own preview still uses the truth.
    r = await client.call_tool("preview_action", {"game_id": gid, "faction": "insurgent",
                                                   "action": {"action_type": "ambush", "source_province": "hodh_ech_chargui", "target_province": "tagant", "forces_committed": 300}})
    assert r.is_error and "insurgent seat is open" in r.content[0].text


@pytest.mark.anyio
async def test_estimate_is_noisy_lagged_and_moves_over_turns(client: Client):
    g = (await client.call_tool("new_game", {"seed": 7})).structured_content
    gid = g["game_id"]
    full = (await client.call_tool("get_state", {"game_id": gid, "view": "full"})).structured_content
    gov = (await client.call_tool("get_state", {"game_id": gid, "view": "government"})).structured_content
    truth = {p["id"]: p["insurgent_forces"] for p in full["provinces"]}
    est0 = {p["id"]: p["insurgent_estimated"] for p in gov["provinces"]}
    occupied = [pid for pid, t in truth.items() if t > 0]
    assert all(est0[pid] != truth[pid] for pid in occupied), "estimates must not equal the true counts"
    assert all(est0[pid] == 0 for pid, t in truth.items() if t == 0)
    assert gov["global_state"]["faction_forces"]["insurgent_estimated_by_government"] == sum(est0.values())
    # Shares are not the true shares either (that was the leak).
    true_total = sum(truth.values())
    shares_match = [abs(est0[p] / sum(est0.values()) - truth[p] / true_total) < 0.005 for p in occupied]
    assert not all(shares_match)

    from tests import scripted
    await scripted.play(client, gid, turns=3)
    gov3 = (await client.call_tool("get_state", {"game_id": gid, "view": "government"})).structured_content
    est3 = {p["id"]: p["insurgent_estimated"] for p in gov3["provinces"]}
    assert est3 != est0
    # Deterministic: a second seed-7 game with the same script gives the same estimates.
    g2 = (await client.call_tool("new_game", {"seed": 7})).structured_content["game_id"]
    await scripted.play(client, g2, turns=3)
    gov3b = (await client.call_tool("get_state", {"game_id": g2, "view": "government"})).structured_content
    assert {p["id"]: p["insurgent_estimated"] for p in gov3b["provinces"]} == est3
    assert (await client.call_tool("replay", {"game_id": gid})).structured_content["reproduced"] is True


@pytest.mark.anyio
async def test_both_host_seats_keep_the_full_view_and_the_seed(client: Client):
    g = (await client.call_tool("new_game", {"seed": 5})).structured_content
    assert g["seed"] == 5 and g["initial_state"]["view"] == "full" and g["initial_state"]["seed"] == 5
    s = (await client.call_tool("get_state", {"game_id": g["game_id"]})).structured_content
    assert s["view"] == "full" and "insurgent_forces" in s["provinces"][0]
    cards = (await client.call_tool("list_games", {})).structured_content["result"]
    assert cards[0]["seed"] == 5
    assert (await client.call_tool("replay", {"game_id": g["game_id"]})).structured_content["seed"] == 5
