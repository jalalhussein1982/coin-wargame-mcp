"""A fixed ten-turn script for seed 7, used by the replay, learning and eval tests.

Government alternates clear/hold/build moves with a patrol; the insurgent plays an
AQ_NETWORK line (smuggling, propaganda, ambush, taxation). One IC request on turn 3.
Every action is legal from the initial state regardless of dice outcomes because the
provinces involved start with ample forces and resources are never exhausted.
"""

from __future__ import annotations

GOV = [
    {"action_type": "patrol", "source_province": "hodh_ech_chargui", "forces_committed": 500},
    {"action_type": "tribal_engagement", "source_province": "hodh_el_gharbi", "forces_committed": 0},
    {"action_type": "intelligence_operation", "source_province": "assaba", "forces_committed": 100},
    {"action_type": "patrol", "source_province": "guidimaka", "forces_committed": 300},
    {"action_type": "media_campaign", "source_province": "nouakchott", "forces_committed": 0},
    {"action_type": "checkpoint_network", "source_province": "assaba", "forces_committed": 200},
    {"action_type": "patrol", "source_province": "tagant", "forces_committed": 300},
    {"action_type": "tribal_engagement", "source_province": "hodh_ech_chargui", "forces_committed": 0},
    {"action_type": "patrol", "source_province": "adrar", "forces_committed": 300},
    {"action_type": "media_campaign", "source_province": "trarza", "forces_committed": 0},
]
INS = [
    {"action_type": "smuggling_operation", "source_province": "hodh_ech_chargui", "forces_committed": 100},
    {"action_type": "propaganda", "source_province": "hodh_el_gharbi", "forces_committed": 0},
    {"action_type": "taxation", "source_province": "hodh_ech_chargui", "forces_committed": 50},
    {"action_type": "hit_and_run", "source_province": "hodh_el_gharbi", "forces_committed": 100},
    {"action_type": "propaganda", "source_province": "guidimaka", "forces_committed": 0},
    {"action_type": "smuggling_operation", "source_province": "hodh_el_gharbi", "forces_committed": 100},
    {"action_type": "taxation", "source_province": "hodh_el_gharbi", "forces_committed": 50},
    {"action_type": "propaganda", "source_province": "assaba", "forces_committed": 0},
    {"action_type": "taxation", "source_province": "hodh_ech_chargui", "forces_committed": 50},
    {"action_type": "intimidation", "source_province": "guidimaka", "forces_committed": 100},
]
IC_TURN = 3
IC = {"request_type": "military_aid", "justification": "Mali incursion active; garrison mutiny in the east",
      "target": "France"}


async def play(client, game_id: str, turns: int = 10) -> list[dict]:
    """Play the script through the MCP client. Returns the TurnResults in order."""
    results = []
    for t in range(turns):
        if t + 1 == IC_TURN:
            r = await client.call_tool("request_international_community", {"game_id": game_id, **IC})
            assert not r.is_error, r.content[0].text
        r = await client.call_tool("submit_action", {"game_id": game_id, "faction": "government", "action": GOV[t],
                                                      "rationale": f"turn {t + 1}: clear_hold_build"})
        assert not r.is_error, r.content[0].text
        results.append(r.structured_content)
        if r.structured_content["status"] == "ended":
            break
        r = await client.call_tool("submit_action", {"game_id": game_id, "faction": "insurgent", "action": INS[t],
                                                      "rationale": f"turn {t + 1}: AQ_NETWORK line"})
        assert not r.is_error, r.content[0].text
        results.append(r.structured_content)
        if r.structured_content["status"] == "ended":
            break
    return results
