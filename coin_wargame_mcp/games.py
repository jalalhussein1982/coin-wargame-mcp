"""Game persistence: one SQLite row per game, optimistic versioning.

The server keeps nothing in memory between requests. Every tool call loads a
``GameRecord`` by its handle, rebuilds the engine from it, acts, and writes the
row back with ``save(rec, expected_version)``. A write that finds a different
version raises ``StaleVersion`` and the caller tells the model to re-read.

Row layout (spec §7): ``state_json`` is ``GameStateStore.serialize()``;
``controller_json`` is the phase/turn bookkeeping; ``dice_state`` is
``random.Random.getstate()`` in JSON-safe form; ``turn_log_json`` is this
server's own log with ``faction`` at the top level of each entry.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(os.environ.get("COIN_WARGAME_DB", "games.db"))


class UnknownGame(LookupError):
    """No row for that game_id."""


class StaleVersion(RuntimeError):
    """The row changed since it was read."""


def dice_state_to_json(state: tuple) -> list:
    """``random.Random.getstate()`` -> JSON-safe list (tuples become lists)."""
    version, internal, gauss_next = state
    return [version, list(internal), gauss_next]


def dice_state_from_json(data: list) -> tuple:
    """Inverse of :func:`dice_state_to_json`; ``setstate`` requires tuples."""
    version, internal, gauss_next = data
    return (version, tuple(internal), gauss_next)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class GameRecord:
    game_id: str
    seed: int
    scenario: str
    state: dict
    controller: dict
    dice_state: list
    turn_log: list[dict]
    status: str
    seats: dict
    version: int
    created_at: str
    updated_at: str
    winner: dict | None = None
    seat_tokens: dict = field(default_factory=dict)

    def copy(self) -> GameRecord:
        return replace(
            self,
            state=json.loads(json.dumps(self.state)),
            controller=dict(self.controller),
            dice_state=list(self.dice_state),
            turn_log=list(self.turn_log),
            seats=dict(self.seats),
            seat_tokens=dict(self.seat_tokens),
            winner=dict(self.winner) if self.winner else None,
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id         TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    seed            INTEGER NOT NULL,
    scenario        TEXT NOT NULL,
    state_json      TEXT NOT NULL,
    controller_json TEXT NOT NULL,
    dice_state      TEXT NOT NULL,
    turn_log_json   TEXT NOT NULL,
    status          TEXT NOT NULL,
    seats_json      TEXT NOT NULL,
    seat_tokens_json TEXT NOT NULL DEFAULT '{}',
    winner_json     TEXT,
    version         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS games_status_updated ON games(status, updated_at);
"""

_COLUMNS = (
    "game_id, created_at, updated_at, seed, scenario, state_json, controller_json, dice_state, "
    "turn_log_json, status, seats_json, seat_tokens_json, winner_json, version"
)


class GameStore:
    """SQLite-backed store. Safe for concurrent writers: ``save`` is a compare-and-swap."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    @staticmethod
    def _row_to_record(row: tuple) -> GameRecord:
        (game_id, created_at, updated_at, seed, scenario, state_json, controller_json, dice_state,
         turn_log_json, status, seats_json, seat_tokens_json, winner_json, version) = row
        return GameRecord(
            game_id=game_id, seed=seed, scenario=scenario,
            state=json.loads(state_json), controller=json.loads(controller_json),
            dice_state=json.loads(dice_state), turn_log=json.loads(turn_log_json),
            status=status, seats=json.loads(seats_json), seat_tokens=json.loads(seat_tokens_json),
            winner=json.loads(winner_json) if winner_json else None,
            version=version, created_at=created_at, updated_at=updated_at,
        )

    def create(
        self,
        seed: int,
        scenario: str,
        state: dict,
        controller: dict,
        dice_state: list,
        seats: dict,
        seat_tokens: dict | None = None,
    ) -> GameRecord:
        now = _now()
        rec = GameRecord(
            game_id=uuid.uuid4().hex[:12], seed=seed, scenario=scenario, state=state,
            controller=controller, dice_state=dice_state, turn_log=[], status="active",
            seats=seats, seat_tokens=seat_tokens or {}, version=1, created_at=now, updated_at=now,
        )
        with self._conn() as c:
            c.execute(
                f"INSERT INTO games ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.game_id, now, now, seed, scenario, json.dumps(state), json.dumps(controller),
                    json.dumps(dice_state), "[]", rec.status, json.dumps(seats),
                    json.dumps(rec.seat_tokens), None, rec.version,
                ),
            )
        return rec

    def get(self, game_id: str) -> GameRecord:
        with self._conn() as c:
            row = c.execute(f"SELECT {_COLUMNS} FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if row is None:
            raise UnknownGame(game_id)
        return self._row_to_record(row)

    def save(self, rec: GameRecord, expected_version: int) -> GameRecord:
        """Write ``rec`` if the row still has ``expected_version``; bump the version."""
        now = _now()
        new_version = expected_version + 1
        with self._conn() as c:
            cur = c.execute(
                "UPDATE games SET updated_at=?, state_json=?, controller_json=?, dice_state=?, "
                "turn_log_json=?, status=?, seats_json=?, seat_tokens_json=?, winner_json=?, version=? "
                "WHERE game_id=? AND version=?",
                (
                    now, json.dumps(rec.state), json.dumps(rec.controller), json.dumps(rec.dice_state),
                    json.dumps(rec.turn_log), rec.status, json.dumps(rec.seats),
                    json.dumps(rec.seat_tokens), json.dumps(rec.winner) if rec.winner else None,
                    new_version, rec.game_id, expected_version,
                ),
            )
            if cur.rowcount != 1:
                exists = c.execute("SELECT 1 FROM games WHERE game_id=?", (rec.game_id,)).fetchone()
                if exists is None:
                    raise UnknownGame(rec.game_id)
                raise StaleVersion(f"{rec.game_id}: expected version {expected_version}")
        rec.version = new_version
        rec.updated_at = now
        return rec

    def list(self, status: str | None = None, limit: int = 20) -> list[GameRecord]:
        q = f"SELECT {_COLUMNS} FROM games"
        args: tuple[Any, ...] = ()
        if status:
            q += " WHERE status = ?"
            args = (status,)
        q += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(q, args + (limit,)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def prune(self, older_than_days: int = 30) -> int:
        """Delete games still ``active`` that have not been touched for N days. Ended games are kept."""
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self._conn() as c:
            cur = c.execute("DELETE FROM games WHERE status = 'active' AND updated_at < ?", (cutoff,))
            return cur.rowcount
