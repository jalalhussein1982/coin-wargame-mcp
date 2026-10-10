"""Two hosts share one game: a write on a stale version fails, exactly one succeeds."""

from __future__ import annotations

import pytest

from coin_wargame_mcp.engine import Engine, make_action
from coin_wargame_mcp.games import GameStore, StaleVersion
from mcp import Client

PATROL = {"action_type": "patrol", "source_province": "nouakchott", "forces_committed": 500}


@pytest.mark.anyio
async def test_second_writer_on_the_same_version_is_refused(tmp_path, monkeypatch):
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as a, Client(mcp, raise_exceptions=True) as b:
        game = (await a.call_tool("new_game", {"seed": 11})).structured_content["game_id"]
        # Both clients read version 1. Client A writes first through the tool; client B then
        # tries to write the same version directly through the store, as a racing process would.
        store = GameStore(tmp_path / "games.db")
        stale = store.get(game)
        assert stale.version == 1
        r = (await a.call_tool("submit_action", {"game_id": game, "faction": "government", "action": PATROL})).structured_content
        assert r["version"] == 2
        eng = Engine.load(stale)
        eng.submit("government", make_action("government", PATROL))
        stale.state, stale.controller, stale.dice_state = eng.snapshot()
        with pytest.raises(StaleVersion):
            store.save(stale, expected_version=1)
        # Client B sees A's move and its own attempt to act for the government is now out of phase.
        s = (await b.call_tool("get_state", {"game_id": game})).structured_content
        assert s["version"] == 2 and s["phase"] == "insurgent_action"
        r = await b.call_tool("submit_action", {"game_id": game, "faction": "government", "action": PATROL})
        assert r.is_error and "government's turn" not in r.content[0].text  # it is the insurgent's turn now
        assert "insurgent's turn" in r.content[0].text


@pytest.mark.anyio
async def test_stale_version_surfaces_as_the_documented_tool_error(monkeypatch):
    """Simulate the race inside the server: the row moves between load and save."""
    from coin_wargame_mcp import server as srv

    async with Client(srv.mcp, raise_exceptions=True) as c:
        game = (await c.call_tool("new_game", {"seed": 12})).structured_content["game_id"]
        real_load = srv._load

        def racing_load(game_id):
            rec = real_load(game_id)
            # Another writer lands between our read and our write.
            other = real_load(game_id)
            srv._store().save(other, expected_version=other.version)
            return rec

        monkeypatch.setattr(srv, "_load", racing_load)
        r = await c.call_tool("submit_action", {"game_id": game, "faction": "government", "action": PATROL})
        assert r.is_error
        assert "game advanced since you read it; call get_state" in r.content[0].text
