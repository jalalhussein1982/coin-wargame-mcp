"""The adjudication recipe, legal-action enumeration and replay over the vendored engine.

``Engine`` holds a live ``GameStateStore``, the phase bookkeeping and a seeded
``DiceRoller``. It is rebuilt from a ``GameRecord`` on every tool call and
snapshotted back afterwards; nothing here is cached between requests.

Determinism: every random draw goes through the game's ``DiceRoller``. Two engine
paths are wrapped because they are not seeded upstream:

* ``SessionController.advance_turn`` stamps ``datetime.now()`` into
  ``global_state.turn_history``, which is part of the serialized state. The phase
  machine is therefore mirrored here (same four phases, same transitions) and the
  turn record carries no wall-clock field.
* ``ICOracle._fallback_evaluate`` calls the module-level ``random.random()``. The
  request is passed through ``ic_request`` which seeds a private ``random.Random``
  from the game dice and swaps it in for the duration of the call.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from . import _engine as E
from .games import GameRecord, dice_state_from_json, dice_state_to_json

PHASES = [p.value for p in E.TurnPhase]
ACTION_PHASE = {"government": "government_action", "insurgent": "insurgent_action"}

# Action types whose validator checks adjacency when a target is given.
ADJACENCY_TYPES = {
    E.ActionType.ASSAULT, E.ActionType.RAID, E.ActionType.REINFORCE, E.ActionType.DEPLOY,
    E.ActionType.AMBUSH, E.ActionType.TERRITORIAL_SEIZURE, E.ActionType.COMPLEX_ATTACK,
    E.ActionType.HIT_AND_RUN,
}
IC_REQUEST_TYPES = ("military_aid", "sanctions", "diplomatic_pressure", "humanitarian", "peacekeeping")


class IllegalAction(ValueError):
    def __init__(self, errors: list[str], alternatives: list[dict]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.alternatives = alternatives


class WrongTurn(RuntimeError):
    """Not this faction's action phase."""


class GameOver(RuntimeError):
    """The game has ended; read-only tools still work."""


@dataclass
class SubmitResult:
    adjudication: dict
    mutations: list[dict]
    victory: dict | None
    log_entry: dict


def make_action(faction: str, data: dict) -> E.Action:
    """Build the engine's faction-specific action model from a plain dict.

    Accepts a ``LegalAction`` dict as-is: when ``forces_committed`` is absent, the
    ``min_forces`` it carries is used, so every row of ``list_legal_actions`` is
    submittable without edits."""
    cls = E.GovernmentAction if faction == "government" else E.InsurgentAction
    payload = {k: v for k, v in data.items() if k in {
        "action_type", "source_province", "target_province", "forces_committed", "resources_spent"}}
    if "forces_committed" not in payload and "min_forces" in data:
        payload["forces_committed"] = data["min_forces"]
    return cls(**payload)


def action_dump(action: E.Action) -> dict:
    d = action.model_dump(mode="json")
    d.pop("faction", None)
    return d


class Engine:
    def __init__(self, gs: E.GameStateStore, controller: dict, dice: E.DiceRoller, seed: int) -> None:
        self.gs = gs
        self.controller = controller
        self.dice = dice
        self.seed = seed
        self._resolver = E.OutcomeResolver(E.PROBABILITY_TABLES_PATH)

    # ── construction / persistence ─────────────────────────────────────────────

    @classmethod
    def new(cls, seed: int, scenario: str = "mauritania_default") -> Engine:
        if scenario != "mauritania_default":
            raise ValueError(f"unknown scenario {scenario!r}; only 'mauritania_default' exists")
        gs = E.GameStateStore()
        gs.load_initial_state(E.INITIAL_STATE_PATH)
        controller = {"phase": "government_action", "session_state": "active", "actions_this_turn": []}
        return cls(gs, controller, E.DiceRoller(seed), seed)

    @classmethod
    def load(cls, rec: GameRecord) -> Engine:
        gs = E.GameStateStore.deserialize(rec.state)
        dice = E.DiceRoller(rec.seed)
        dice._rng.setstate(dice_state_from_json(rec.dice_state))
        controller = {
            "phase": rec.controller.get("phase", "government_action"),
            "session_state": rec.controller.get("session_state", "active"),
            "actions_this_turn": list(rec.controller.get("actions_this_turn", [])),
        }
        return cls(gs, controller, dice, rec.seed)

    def snapshot(self) -> tuple[dict, dict, list]:
        return self.gs.serialize(), dict(self.controller), dice_state_to_json(self.dice._rng.getstate())

    # ── read side ──────────────────────────────────────────────────────────────

    @property
    def global_state(self):
        return self.gs.get_global_state()

    @property
    def turn(self) -> int:
        return self.global_state.turn

    @property
    def phase(self) -> str:
        return self.controller["phase"]

    @property
    def active_faction(self) -> str:
        return self.global_state.active_faction

    def _session_controller(self) -> E.SessionController:
        """A SessionController bound to our state, for check_victory_conditions/end_session."""
        sc = E.SessionController()
        sc._game_state = self.gs
        sc._state = E.SessionState.ACTIVE
        sc._turn_phase = E.TurnPhase(self.phase)
        sc._actions_this_turn = list(self.controller["actions_this_turn"])
        return sc

    def check_victory(self) -> dict | None:
        v = self._session_controller().check_victory_conditions()
        return {"winner": v.winner, "condition": v.condition, "turn": v.turn} if v else None

    def end_summary(self) -> dict:
        return self._session_controller().end_session()

    def legal_actions(self, faction: str, province: str | None = None) -> list[dict]:
        """Every (type, source, target) the validator accepts right now, with the minimum legal
        commitment: ``MIN_FORCE_REQUIREMENTS`` forces and no extra resources."""
        if faction not in ACTION_PHASE:
            raise ValueError(f"unknown faction {faction!r}")
        if province is not None and province not in E.ALL_PROVINCE_IDS:
            raise ValueError(f"unknown province {province!r}")
        sources = [province] if province else list(E.ALL_PROVINCE_IDS)
        out: list[dict] = []
        for atype in sorted(E.rules_engine.get_valid_actions_for_faction(faction), key=lambda a: a.value):
            min_forces = E.rules_engine.MIN_FORCE_REQUIREMENTS.get(atype, 0)
            cost = E.rules_engine.ACTION_COSTS.get(atype, 0)
            for src in sources:
                targets: list[str | None] = [None]
                if atype in ADJACENCY_TYPES:
                    targets = [None] + list(E.rules_engine.PROVINCE_ADJACENCY[src])
                for tgt in targets:
                    action = make_action(faction, {
                        "action_type": atype, "source_province": src, "target_province": tgt,
                        "forces_committed": min_forces, "resources_spent": 0,
                    })
                    v = E.rules_engine.validate_action(action, self.gs)
                    if v.is_valid:
                        out.append({
                            "action_type": atype.value, "source_province": src, "target_province": tgt,
                            "cost": cost, "min_forces": min_forces, "warnings": list(v.warnings),
                        })
        return out

    def validate(self, faction: str, action: E.Action) -> E.ActionValidation:
        return E.rules_engine.validate_action(action, self.gs)

    def preview(self, faction: str, action: E.Action) -> dict:
        return self._resolver.get_probability_breakdown(action, self.gs)

    def recommend_doctrine(self) -> tuple[str, str]:
        d = E.doctrine_selector.select_doctrine(self.gs)
        return d.value, E.doctrine_selector.get_doctrine_description(d)

    # ── phase machine (mirror of SessionController.advance_phase, without the timestamp) ──

    def _advance_phase(self) -> None:
        gs = self.global_state
        phase = self.phase
        if phase == "government_action":
            self.controller["phase"] = "government_resolution"
        elif phase == "government_resolution":
            self.controller["phase"] = "insurgent_action"
            gs.active_faction = "insurgent"
        elif phase == "insurgent_action":
            self.controller["phase"] = "insurgent_resolution"
        elif phase == "insurgent_resolution":
            self._advance_turn()

    def _advance_turn(self) -> None:
        gs = self.global_state
        actions = list(self.controller["actions_this_turn"])
        gov = next((a for a in actions if a.get("faction") == "government" and a.get("action")), None)
        control_changes = sum(
            1 for a in actions for m in a.get("outcome", {}).get("mutations", [])
            if str(m.get("target", "")).endswith(".control")
        )
        gs.turn_history.append({
            "turn": gs.turn,
            "actions": actions,
            # `state_encoder` reads this key for the GOVERNMENT PATTERNS brief section and
            # `doctrine_selector` reads `control_changes`; the engine's own controller writes neither.
            "government_action": gov["action"] if gov else {},
            "control_changes": control_changes,
        })
        gs.turn += 1
        gs.active_faction = "government"
        self.controller["phase"] = "government_action"
        self.controller["actions_this_turn"] = []

    def _record(self, entry: dict) -> None:
        self.controller["actions_this_turn"].append(entry)

    def _require_turn(self, faction: str) -> None:
        if self.controller["session_state"] != "active":
            raise GameOver("game is over")
        if faction not in ACTION_PHASE:
            raise ValueError(f"unknown faction {faction!r}")
        if self.active_faction != faction or self.phase != ACTION_PHASE[faction]:
            raise WrongTurn(
                f"it is the {self.active_faction}'s turn (phase {self.phase}); "
                f"{faction} may act only in phase {ACTION_PHASE[faction]}"
            )

    # ── write side ─────────────────────────────────────────────────────────────

    def submit(self, faction: str, action: E.Action, rationale: str | None = None) -> SubmitResult:
        self._require_turn(faction)
        v = self.validate(faction, action)
        if not v.is_valid:
            raise IllegalAction(list(v.errors), self.legal_actions(faction, action.source_province)[:3])
        is_gov = faction == "government"
        res = self._resolver.resolve(action, self.gs, self.dice)
        muts = E.StateMutator().apply_outcome(res, self.gs)
        if res.cascading_effects:
            cascade = E.CascadeEngine().process_cascades(
                res.cascading_effects, action.source_province, res.outcome, is_gov, self.gs,
            )
            if cascade:
                self.gs.apply_mutations(cascade)
            muts = muts + cascade
        mut_dumps = [m.model_dump(mode="json") for m in muts]
        adjudication = res.to_adjudication_result().model_dump(mode="json")
        adjudication["mutations"] = mut_dumps
        adjudication["action"] = action_dump(action)
        adjudication["base_probability"] = res.base_probability
        adjudication["modifier_total"] = res.modifier_result.total
        adjudication["attacker_casualties"] = res.attacker_casualties
        adjudication["defender_casualties"] = res.defender_casualties
        turn = self.turn
        outcome_blob = {"outcome": res.outcome.value, "mutations": mut_dumps,
                        "success_roll": res.success_roll, "required_roll": res.required_roll}
        self._record({"faction": faction, "action": action_dump(action), "outcome": outcome_blob,
                      "rationale": rationale or ""})
        self._advance_phase()
        self._advance_phase()
        self.gs.recalculate_force_totals()
        victory = self.check_victory()
        if victory:
            self.controller["session_state"] = "ended"
        entry = {
            "kind": "action", "turn": turn, "faction": faction, "action": action_dump(action),
            "rationale": rationale or "", "adjudication": adjudication, "mutations": mut_dumps,
            "victory": victory,
        }
        return SubmitResult(adjudication, mut_dumps, victory, entry)

    def pass_turn(self, faction: str) -> SubmitResult:
        self._require_turn(faction)
        turn = self.turn
        self._record({"faction": faction, "action": None, "outcome": {"outcome": "passed", "mutations": []},
                      "rationale": ""})
        self._advance_phase()
        self._advance_phase()
        self.gs.recalculate_force_totals()
        victory = self.check_victory()
        if victory:
            self.controller["session_state"] = "ended"
        entry = {"kind": "pass", "turn": turn, "faction": faction, "action": None, "rationale": "",
                 "adjudication": None, "mutations": [], "victory": victory}
        return SubmitResult({}, [], victory, entry)

    def ic_request(self, request_type: str, justification: str, target: str | None) -> dict:
        """ICOracle rule-based path with its unseeded ``random.random()`` replaced by a draw
        seeded from the game dice, so a replay reproduces the same decision."""
        if request_type not in IC_REQUEST_TYPES:
            raise ValueError(f"unknown request_type {request_type!r}; one of {', '.join(IC_REQUEST_TYPES)}")
        if self.controller["session_state"] != "active":
            raise GameOver("game is over")
        oracle = E.ICOracle(api_key=None)
        oracle._model = None
        req = E.ICRequest(request_type=request_type, requesting_faction="government",
                          justification=justification, target=target)
        # `_fallback_evaluate` does `import random` inside the method and calls the module-level
        # `random.random()`. Seed that global generator from the game dice for the duration of the
        # call and restore its prior state afterwards, so nothing else in the process is affected.
        saved = random.getstate()
        random.seed(self.dice.roll(10**6))
        try:
            resp = oracle._fallback_evaluate(req, self.gs)
        finally:
            random.setstate(saved)
        out = {
            "decision": resp.decision, "conditions": list(resp.conditions), "magnitude": resp.magnitude,
            "timeline": resp.timeline, "backlash_effects": list(resp.backlash_effects),
            "narrative": resp.narrative, "request_type": request_type, "target": target,
        }
        return out

    # ── replay ─────────────────────────────────────────────────────────────────

    @classmethod
    def replay(cls, rec: GameRecord) -> dict:
        """Re-run ``rec.turn_log`` from the initial state with ``rec.seed`` and compare."""
        eng = cls.new(rec.seed, rec.scenario)
        divergence: int | None = None
        for entry in rec.turn_log:
            kind = entry.get("kind")
            try:
                if kind == "action":
                    action = make_action(entry["faction"], entry["action"])
                    res = eng.submit(entry["faction"], action, entry.get("rationale"))
                    if res.adjudication.get("outcome") != entry["adjudication"].get("outcome") or \
                            res.adjudication.get("success_roll") != entry["adjudication"].get("success_roll"):
                        divergence = entry["turn"]
                        break
                elif kind == "pass":
                    eng.pass_turn(entry["faction"])
                elif kind == "ic_request":
                    ic = entry["ic"]
                    got = eng.ic_request(ic["request_type"], ic.get("justification", ""), ic.get("target"))
                    if got["decision"] != ic["decision"]:
                        divergence = entry["turn"]
                        break
            except (IllegalAction, WrongTurn, GameOver):
                divergence = entry.get("turn")
                break
        final_state, _, _ = eng.snapshot()
        reproduced = divergence is None and final_state == rec.state
        if not reproduced and divergence is None:
            divergence = rec.state.get("global_state", {}).get("turn")
        return {"reproduced": reproduced, "divergence_at_turn": divergence, "final_state": final_state}
