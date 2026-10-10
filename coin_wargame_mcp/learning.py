"""Learning tools over the server's turn log, through the engine's rule-based paths.

The engine's learning classes read a turn history shaped like::

    [{"turn": 3, "actions": [{"faction": "government", "action": {...},
                               "outcome": {"outcome": "success", "mutations": [...]}}]}]

Its own ``SessionController.record_action`` never puts ``faction`` at that level (it
is nested inside ``action``), which is why ``StrategyClassifier`` saw zero government
actions upstream. The server's log carries ``faction`` at the top level and
``engine_history`` regroups it per turn, so all four learning tools see every move.
"""

from __future__ import annotations

from . import _engine as E
from .games import GameRecord

DOCTRINES = ("AQ_NETWORK", "ISIS_TERRITORIAL", "TALIBAN_ADAPTIVE")


def engine_history(rec: GameRecord) -> list[dict]:
    by_turn: dict[int, list[dict]] = {}
    for e in rec.turn_log:
        if e.get("kind") != "action":
            continue
        entry = {
            "faction": e["faction"],
            "action": dict(e["action"]),
            "outcome": {
                "outcome": e["adjudication"]["outcome"],
                "mutations": list(e.get("mutations", [])),
                "success_roll": e["adjudication"].get("success_roll"),
                "required_roll": e["adjudication"].get("required_roll"),
            },
            "rationale": e.get("rationale", ""),
        }
        if e["faction"] == "insurgent":
            doctrine = next((d for d in DOCTRINES if d in (e.get("rationale") or "").upper()), "")
            if doctrine:
                entry["doctrine_used"] = doctrine
        by_turn.setdefault(e["turn"], []).append(entry)
    return [{"turn": t, "actions": acts} for t, acts in sorted(by_turn.items())]


def government_outcome(rec: GameRecord) -> str:
    w = (rec.winner or {}).get("winner")
    return "victory" if w == "government" else "defeat" if w == "insurgent" else "stalemate"


def feedback(rec: GameRecord, gs: E.GameStateStore, turn: int | None) -> dict:
    gov = [e for e in rec.turn_log if e.get("kind") == "action" and e["faction"] == "government"]
    if not gov:
        raise LookupError("no government action has been played yet")
    if turn is None:
        e = gov[-1]
    else:
        matches = [x for x in gov if x["turn"] == turn]
        if not matches:
            played = sorted({x["turn"] for x in gov})
            raise LookupError(f"no government action on turn {turn}; turns with one: {played}")
        e = matches[-1]
    action = E.GovernmentAction(**{k: v for k, v in e["action"].items()
                                   if k in ("action_type", "source_province", "target_province",
                                            "forces_committed", "resources_spent")})
    adj = e["adjudication"]
    adjudication = E.AdjudicationResult(
        action=action, outcome=E.OutcomeType(adj["outcome"]), success_roll=adj["success_roll"],
        required_roll=adj["required_roll"], mutations=[E.Mutation(**m) for m in e.get("mutations", [])],
        narrative=adj.get("narrative", ""), cascading_effects=list(adj.get("cascading_effects", [])),
    )
    fb = E.PostTurnAnalyzer(api_key=None)._analyze_rule_based(action, adjudication, gs, None)
    out = fb.to_dict()
    out.update({"turn": e["turn"], "action": e["action"], "outcome": adj["outcome"]})
    return out


def aar(rec: GameRecord, gs: E.GameStateStore) -> dict:
    outcome = government_outcome(rec)
    review = E.AARGenerator(api_key=None)._generate_rule_based(engine_history(rec), gs, outcome)
    out = review.to_dict()
    out["outcome"] = outcome
    return out


def strategy(rec: GameRecord) -> dict:
    hist = engine_history(rec)
    profile = E.StrategyClassifier(api_key=None).classify_from_turn_history(hist)
    d = profile.to_dict()
    d["government_actions_seen"] = sum(
        1 for t in hist for a in t["actions"] if a["faction"] == "government")
    return d


def patterns(rec: GameRecord) -> dict:
    return E.PatternDetector().detect_patterns(engine_history(rec)).to_dict()
