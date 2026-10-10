"""GameStore: round-trips, optimistic versioning, dice-state persistence."""

from __future__ import annotations

import random

import pytest

from coin_wargame_mcp import _engine as E
from coin_wargame_mcp.games import (
    GameStore,
    StaleVersion,
    UnknownGame,
    dice_state_from_json,
    dice_state_to_json,
)


@pytest.fixture
def store(tmp_path):
    return GameStore(tmp_path / "t.db")


def _initial_state() -> dict:
    gs = E.GameStateStore()
    gs.load_initial_state(E.INITIAL_STATE_PATH)
    return gs.serialize()


def test_round_trip_of_serialized_state(store):
    state = _initial_state()
    dice = E.DiceRoller(seed=42)
    rec = store.create(
        seed=42, scenario="mauritania_default", state=state,
        controller={"turn": 1, "phase": "government_action"},
        dice_state=dice_state_to_json(dice._rng.getstate()),
        seats={"government": "host", "insurgent": "host"},
    )
    back = store.get(rec.game_id)
    assert back.state == state
    assert E.GameStateStore.deserialize(back.state).get_province("trarza").government_forces == 2500
    assert back.version == 1 and back.status == "active" and back.turn_log == []


def test_save_with_stale_version_raises(store):
    rec = store.create(1, "s", {"a": 1}, {}, dice_state_to_json(random.Random(1).getstate()), {})
    other = store.get(rec.game_id)
    store.save(rec, expected_version=1)
    assert rec.version == 2
    other.state = {"a": 2}
    with pytest.raises(StaleVersion):
        store.save(other, expected_version=1)
    assert store.get(rec.game_id).state == {"a": 1}


def test_dice_state_round_trip_reproduces_next_roll(store):
    dice = E.DiceRoller(seed=7)
    for _ in range(5):
        dice.roll(100)
    saved = dice_state_to_json(dice._rng.getstate())
    expected = dice.roll(100)
    rec = store.create(7, "s", {}, {}, saved, {})
    restored = E.DiceRoller(seed=7)
    restored._rng.setstate(dice_state_from_json(store.get(rec.game_id).dice_state))
    assert restored.roll(100) == expected


def test_unknown_game_and_listing_and_prune(store):
    with pytest.raises(UnknownGame):
        store.get("nope")
    a = store.create(1, "s", {}, {}, [3, [0] * 625, None], {})
    b = store.create(2, "s", {}, {}, [3, [0] * 625, None], {})
    b.status = "ended"
    store.save(b, 1)
    assert {r.game_id for r in store.list()} == {a.game_id, b.game_id}
    assert [r.game_id for r in store.list(status="ended")] == [b.game_id]
    assert store.prune(older_than_days=1) == 0  # updated just now, inside the window
    # Backdate the active game and prune it; the ended one survives.
    import sqlite3
    with sqlite3.connect(store.path) as c:
        c.execute("UPDATE games SET updated_at='2000-01-01T00:00:00+00:00' WHERE game_id=?", (a.game_id,))
        c.execute("UPDATE games SET updated_at='2000-01-01T00:00:00+00:00' WHERE game_id=?", (b.game_id,))
    assert store.prune(older_than_days=30) == 1
    assert [r.game_id for r in store.list()] == [b.game_id]
