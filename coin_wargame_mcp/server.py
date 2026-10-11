"""MCP server over the Mauritania COIN wargame engine.

    uv run python -m coin_wargame_mcp.server                 # stdio, for a host
    uv run python -m coin_wargame_mcp.server --http --no-auth  # loopback HTTP for the Inspector / two hosts
    uv run mcp dev coin_wargame_mcp/server.py                # MCP Inspector

What a host model gets, and why each piece exists:

* tools     — lifecycle (``new_game`` mints a handle; nothing else is remembered about the
              caller), reading the board, acting (``submit_action`` runs the engine's full
              adjudication recipe), and the engine's learning layer (feedback, AAR, strategy,
              patterns) over the game's own log.
* resources — doctrine texts and the rules as data (action costs, minimum forces, adjacency,
              probability tables, map), so the model quotes the rules instead of guessing them;
              ``wargame://game/{id}`` and ``.../log`` for the raw record.
* prompts   — ``play_red_team`` / ``play_government`` turn the engine's Red Team system prompt
              into tool-use instructions; ``after_action_debrief`` fixes the AAR structure.

Design choices, stated once here and once in the README:

* Stateless handles. Every tool takes ``game_id``; each call loads the SQLite row, rebuilds
  the engine, acts, and writes back with a version check. Two hosts can share one game.
* Seeded dice persisted between calls, so ``replay`` can re-run the log and prove the state.
* No LLM in the server. ``GEMINI_API_KEY`` is removed in the shim; the engine's rule-based
  paths run. The engine's two unseeded paths are wrapped in ``engine.py``.
* ``ToolError`` for anything the model can fix by choosing differently (unknown game, wrong
  seat or phase, illegal action with alternatives, stale version, finished game). Engine
  exceptions are crashes and stay crashes.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys
from dataclasses import replace
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, Field

from mcp.server import MCPServer
from mcp.server.caching import CacheHint
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations

from . import _engine as E
from . import brief as B
from . import learning as L
from . import seats as S
from .engine import IC_REQUEST_TYPES, Engine, GameOver, IllegalAction, WrongTurn, make_action
from .games import GameRecord, GameStore, StaleVersion, UnknownGame
from .models import (
    AARView,
    Action,
    Brief,
    DoctrineAdvice,
    Faction,
    FoggedProvinceView,
    ForcesView,
    GameCard,
    GameCreated,
    GameStateView,
    GameSummary,
    GlobalStateView,
    ICResponseView,
    LegalAction,
    LegalActions,
    NextTurn,
    PatternView,
    PostTurnFeedbackView,
    ProbabilityBreakdown,
    ProvinceDetail,
    ProvinceView,
    Replay,
    SeatMode,
    SeatTokens,
    StrategyProfileView,
    TurnResult,
    VictoryView,
    View,
)

log = logging.getLogger("coin_wargame_mcp")

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
WRITE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

HOUR_MS = 3_600_000
PUBLIC_RESOURCE_PREFIXES = ("wargame://doctrine/", "wargame://rules/")


async def _resource_cache_hints(ctx, call_next):
    """Per-URI freshness: rules and doctrine are public and stable (1 h); game rows are
    private and never fresh (0 ms). The server-wide ``cache_hints`` cover the lists.

    The lowlevel server hands middleware the handler result as a wire-shaped dict
    (``ttlMs``/``cacheScope``) once the server-wide hint has been applied, so this
    overrides those two keys; a model result is handled the same way for safety."""
    result = await call_next(ctx)
    if ctx.method != "resources/read" or result is None:
        return result
    uri = str((ctx.params or {}).get("uri", ""))
    public = uri.startswith(PUBLIC_RESOURCE_PREFIXES)
    ttl, scope = (HOUR_MS, "public") if public else (0, "private")
    if isinstance(result, dict):
        result["ttlMs"], result["cacheScope"] = ttl, scope
        return result
    if hasattr(result, "model_copy"):
        return result.model_copy(update={"ttl_ms": ttl, "cache_scope": scope})
    return result


def build_server(token_verifier=None, auth=None) -> MCPServer:
    return MCPServer(
        "coin-wargame",
        instructions=(
            "A counterinsurgency wargame (Mauritania, 13 provinces, two seats). Start with new_game, "
            "which returns a game_id you pass to every other tool. Read situation_brief and "
            "list_legal_actions for your faction, preview_action before committing, then submit_action "
            "with a rationale. Illegal actions are refused with the engine's reasons and legal alternatives. "
            "Read wargame://rules/actions for costs and minimum forces. After the game ends, "
            "after_action_review, strategy_profile and pattern_report explain the campaign."
        ),
        token_verifier=token_verifier,
        auth=auth,
        cache_hints={
            "tools/list": CacheHint(ttl_ms=HOUR_MS, scope="public"),
            "prompts/list": CacheHint(ttl_ms=HOUR_MS, scope="public"),
            "resources/list": CacheHint(ttl_ms=HOUR_MS, scope="public"),
            "resources/templates/list": CacheHint(ttl_ms=HOUR_MS, scope="public"),
            "resources/read": CacheHint(ttl_ms=0, scope="private"),
        },
        middleware=[_resource_cache_hints],
        log_level="WARNING",  # the engine logs every resolution at INFO; keep stderr readable
    )


mcp = build_server()


# ── plumbing ─────────────────────────────────────────────────────────────────────────


def _store() -> GameStore:
    return GameStore(os.environ.get("COIN_WARGAME_DB", "games.db"))


def _load(game_id: str) -> GameRecord:
    try:
        return _store().get(game_id)
    except UnknownGame:
        raise ToolError(f"no game {game_id!r}. Call list_games, or new_game to start one.")


def _victory(v: dict | None) -> VictoryView | None:
    return VictoryView(**v) if v else None


def _open(rec: GameRecord, faction: str) -> bool:
    return rec.seats.get(faction, "host") == "open"


def _holds(rec: GameRecord, faction: str, token: str | None) -> bool:
    """A caller holds a seat when it is 'host' (the creating host plays it) or the token matches."""
    if not _open(rec, faction):
        return True
    expected = rec.seat_tokens.get(faction)
    return bool(token and expected and hmac.compare_digest(expected, token))


def _seed_visible(rec: GameRecord) -> bool:
    """A seed predicts every roll, so it is shown only when nobody else is playing: both seats
    held by the caller, or the game over."""
    return rec.status == "ended" or not any(_open(rec, f) for f in S.FACTIONS)


def _require_insurgent_access(rec: GameRecord, token: str | None, what: str) -> None:
    """The insurgent seat sees everything, so full/insurgent data needs that seat while it is open."""
    if rec.status == "active" and not _holds(rec, "insurgent", token):
        raise ToolError(f"the insurgent seat is open; {what} needs its seat_token. "
                        f"Use view='government' (or faction='government') for the fogged picture.")


def _default_view(rec: GameRecord, token: str | None) -> View:
    """What a caller may see without asking: full unless the insurgent seat is someone else's."""
    return "full" if _holds(rec, "insurgent", token) or rec.status == "ended" else "government"


def _resolve_view(rec: GameRecord, view: View | None, token: str | None) -> View:
    if view is None:
        return _default_view(rec, token)
    if view != "government":
        _require_insurgent_access(rec, token, f"view='{view}'")
    return view


def _faction_view(faction: str) -> View:
    return "government" if faction == "government" else "insurgent"


def _state_view(rec: GameRecord, eng: Engine, view: View = "full") -> GameStateView:
    g = eng.global_state
    provinces = eng.gs.get_all_provinces()
    gdump = g.model_dump(mode="json")
    forces = dict(gdump["faction_forces"])
    insurgent_resources: int | None = g.insurgent_resources
    if view == "government":
        est = eng.estimates
        provs: Any = [
            FoggedProvinceView(**{**p.model_dump(mode="json", exclude={"insurgent_forces"}),
                                  "insurgent_estimated": est.get(p.id, 0)})
            for p in provinces
        ]
        forces["insurgent_total"] = None
        forces["insurgent_estimated_by_government"] = sum(est.values())
        insurgent_resources = None
    else:
        provs = [ProvinceView(**p.model_dump(mode="json")) for p in provinces]
    return GameStateView(
        game_id=rec.game_id, seed=rec.seed if (view == "full" and _seed_visible(rec)) else None,
        version=rec.version, status=rec.status,  # type: ignore[arg-type]
        turn=g.turn, phase=eng.phase, active_faction=g.active_faction, view=view,
        global_state=GlobalStateView(
            turn=g.turn, active_faction=g.active_faction, government_resources=g.government_resources,
            insurgent_resources=insurgent_resources, government_legitimacy=g.government_legitimacy,
            faction_forces=ForcesView(**forces), victory_conditions=gdump["victory_conditions"],
            scenario_conditions=gdump["scenario_conditions"],
        ),
        provinces=provs, victory=_victory(rec.winner),
    )


def _commit(rec: GameRecord, eng: Engine, *, entries: list[dict] | None = None) -> GameRecord:
    state, controller, dice = eng.snapshot()
    rec.state, rec.controller, rec.dice_state = state, controller, dice
    if entries:
        digest = eng.state_hash()
        for e in entries:
            e.setdefault("state_hash", digest)
        rec.turn_log = rec.turn_log + entries
    if eng.controller["session_state"] == "ended":
        rec.status = "ended"
    try:
        return _store().save(rec, expected_version=rec.version)
    except StaleVersion:
        raise ToolError("game advanced since you read it; call get_state and choose again")


def _require_active(rec: GameRecord) -> None:
    if rec.status != "active":
        w = (rec.winner or {}).get("winner")
        raise ToolError(f"game is over" + (f" ({w} won on turn {rec.winner['turn']})" if w else "") +
                        "; read-only tools, replay and after_action_review still work")


def _parse_action(faction: str, action: Action) -> E.Action:
    try:
        atype = E.ActionType(action.action_type)
    except ValueError:
        valid = sorted(a.value for a in E.rules_engine.get_valid_actions_for_faction(faction))
        raise ToolError(f"unknown action_type {action.action_type!r}. Valid for {faction}: {', '.join(valid)}")
    for p in (action.source_province, action.target_province):
        if p is not None and p not in E.ALL_PROVINCE_IDS:
            raise ToolError(f"unknown province {p!r}. Provinces: {', '.join(E.ALL_PROVINCE_IDS)}")
    return make_action(faction, {**action.model_dump(), "action_type": atype})


def _turn_result(rec: GameRecord, eng: Engine, faction: str, turn_played: int, res) -> TurnResult:
    return TurnResult(
        game_id=rec.game_id, version=rec.version, turn_played=turn_played, faction=faction,  # type: ignore[arg-type]
        adjudication=res.adjudication or None, mutations=res.mutations, victory=_victory(res.victory),
        status=rec.status,  # type: ignore[arg-type]
        next=NextTurn(turn=eng.turn, active_faction=eng.active_faction, phase=eng.phase),
        new_state=_state_view(rec, eng, _faction_view(faction)),
    )


# ── 4.1 lifecycle ────────────────────────────────────────────────────────────────────


@mcp.tool(title="Start a game", annotations=WRITE)
def new_game(
    seed: Annotated[int | None, Field(ge=0, le=2**32 - 1, description="Dice seed. Same seed and same actions reproduce the same game. Random when omitted.")] = None,
    scenario: Annotated[Literal["mauritania_default"], Field(description="Only one scenario ships with the engine.")] = "mauritania_default",
    seats: Annotated[dict[str, SeatMode] | None, Field(description="Per faction: 'host' (you play it) or 'open' (another host plays it with the returned seat_token). Default: both 'host'.")] = None,
) -> GameCreated:
    """Create a game and return its handle, seat tokens and the initial board as you may see it.

    Both seats default to 'host': one model plays both sides, which is what the benchmark's
    self-play uses. Mark a seat 'open' to hand it to another host over HTTP; the seed is then
    withheld (it predicts every roll) and the board is fogged for the seat you do not hold."""
    import random as _random

    seats = {"government": "host", "insurgent": "host", **(seats or {})}
    bad = set(seats) - set(S.FACTIONS)
    if bad:
        raise ToolError(f"unknown seat(s) {sorted(bad)}; seats are government and insurgent")
    if seed is None:
        seed = _random.SystemRandom().randrange(2**32)
    eng = Engine.new(seed, scenario)
    tokens = {f: S.issue() for f, mode in seats.items() if mode == "open"}
    state, controller, dice = eng.snapshot()
    rec = _store().create(seed=seed, scenario=scenario, state=state, controller=controller,
                          dice_state=dice, seats=seats, seat_tokens=tokens)
    # The creator holds every 'host' seat. Full view only when it holds both; the government view
    # when the insurgent seat is someone else's; the insurgent view when only the government is open.
    if not tokens:
        view: View = "full"
    elif "insurgent" in tokens:
        view = "government"
    else:
        view = "insurgent"
    return GameCreated(game_id=rec.game_id, seed=seed if not tokens else None, seats=seats,
                       seat_tokens=SeatTokens(**tokens), initial_state=_state_view(rec, eng, view))


@mcp.tool(title="List games", annotations=READ)
def list_games(
    status: Annotated[Literal["active", "ended"] | None, Field(description="Filter by status.")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    seat_token: Annotated[str | None, Field(description="Shows games in which this token holds an open seat.")] = None,
) -> list[GameCard]:
    """Games on this server, most recently touched first. Games with an open seat are listed
    only to a caller presenting one of their seat tokens; seeds are shown only when visible."""
    out = []
    for r in _store().list(status, limit):
        if any(_open(r, f) for f in S.FACTIONS) and not any(
                _open(r, f) and _holds(r, f, seat_token) for f in S.FACTIONS):
            continue
        g = r.state["global_state"]
        out.append(GameCard(game_id=r.game_id, seed=r.seed if _seed_visible(r) else None, turn=g["turn"],
                            phase=r.controller.get("phase", "?"),
                            active_faction=g["active_faction"], status=r.status,
                            winner=(r.winner or {}).get("winner"), created_at=r.created_at, updated_at=r.updated_at))
    return out


@mcp.tool(title="End a game", annotations=WRITE_IDEMPOTENT)
def end_game(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    seat_token: Annotated[str | None, Field(description="Required when any seat is open: either player's token ends the game.")] = None,
) -> GameSummary:
    """End a game now and return the engine's session summary.

    Idempotent: ending an ended game returns the same summary. Ended games stay readable
    and after_action_review becomes available. With an open seat, one of the open seats'
    tokens is required so a spectator cannot end someone else's game."""
    rec = _load(game_id)
    open_seats = [f for f in S.FACTIONS if _open(rec, f)]
    if rec.status == "active" and open_seats and not any(_holds(rec, f, seat_token) for f in open_seats):
        raise ToolError(f"the {' and '.join(open_seats)} seat is open; present a seat_token to end the game")
    eng = Engine.load(rec)
    summary = eng.end_summary()
    if rec.status == "active":
        eng.controller["session_state"] = "ended"
        victory = eng.check_victory()
        rec.winner = victory
        rec = _commit(rec, eng, entries=[{"kind": "end", "turn": eng.turn, "faction": None, "victory": victory}])
    return GameSummary(game_id=rec.game_id, seed=rec.seed, status="ended", total_turns=summary["total_turns"],
                       final_state=summary["final_state"], victory=_victory(rec.winner),
                       actions_logged=sum(1 for e in rec.turn_log if e.get("kind") == "action"))


@mcp.tool(title="Replay a game", annotations=READ)
def replay(game_id: Annotated[str, Field(description="Handle from new_game.")]) -> Replay:
    """Re-run the game's turn log from the initial state with its seed and check that it
    rebuilds the stored state exactly. `reproduced` is the reproducibility proof; a False
    with `divergence_at_turn` would mean an unseeded random path in the engine."""
    rec = _load(game_id)
    r = Engine.replay(rec)
    return Replay(game_id=rec.game_id, seed=rec.seed if _seed_visible(rec) else None,
                  actions=sum(1 for e in rec.turn_log if e.get("kind") in ("action", "pass", "ic_request")),
                  reproduced=r["reproduced"], divergence_at_turn=r["divergence_at_turn"],
                  final_turn=rec.state["global_state"]["turn"])


# ── 4.2 reading the board ────────────────────────────────────────────────────────────


@mcp.tool(title="Read the board", annotations=READ)
def get_state(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    view: Annotated[View | None, Field(description="'government' applies the fog rule (estimate instead of true insurgent numbers); 'insurgent' and 'full' see everything and need the insurgent seat while it is open. Default: the most you may see.")] = None,
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, when that seat is open.")] = None,
) -> GameStateView:
    """Turn, phase, active faction, global state and all 13 provinces, as the caller may see them."""
    rec = _load(game_id)
    v = _resolve_view(rec, view, seat_token)
    return _state_view(rec, Engine.load(rec), v)


@mcp.tool(title="Read one province", annotations=READ)
def get_province(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    province_id: Annotated[str, Field(description="One of the 13 province ids, e.g. 'hodh_ech_chargui'.")],
    view: Annotated[View | None, Field(description="As for get_state. Default: the most you may see.")] = None,
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, when that seat is open.")] = None,
) -> ProvinceDetail:
    """One province with the engine's text encoding and how many legal actions each visible faction has from it."""
    rec = _load(game_id)
    eng = Engine.load(rec)
    if province_id not in E.ALL_PROVINCE_IDS:
        raise ToolError(f"unknown province {province_id!r}. Provinces: {', '.join(E.ALL_PROVINCE_IDS)}")
    v = _resolve_view(rec, view, seat_token)
    p = eng.gs.get_province(province_id)
    if v == "government":
        est = eng.estimates.get(province_id, 0)
        fogged = eng.fogged_store().get_province(province_id)
        text = E.state_encoder.encode_province_details(fogged).replace(
            f"Insurgent Forces: {est}", f"Insurgent Forces: ~{est} (estimate)")
        return ProvinceDetail(view=v, province=FoggedProvinceView(**{**p.model_dump(mode="json", exclude={"insurgent_forces"}),
                                                                      "insurgent_estimated": est}),
                              text=text, legal_action_count={"government": len(eng.legal_actions("government", province_id))})
    counts = {f: len(eng.legal_actions(f, province_id)) for f in S.FACTIONS}
    return ProvinceDetail(view=v, province=ProvinceView(**p.model_dump(mode="json")),
                          text=E.state_encoder.encode_province_details(p), legal_action_count=counts)


@mcp.tool(title="Situation brief", annotations=READ)
def situation_brief(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    faction: Annotated[Faction, Field(description="Whose brief. The insurgent brief is the engine's own Red Team encoding.")],
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, needed for the insurgent brief while that seat is open.")] = None,
) -> Brief:
    """A Markdown strategic assessment for one seat: forces, control, the Mali corridor,
    sentiment, resources, scenario conditions, the other side's patterns and opportunities."""
    rec = _load(game_id)
    eng = Engine.load(rec)
    if faction == "insurgent":
        _require_insurgent_access(rec, seat_token, "the insurgent brief")
        text, opps = B.insurgent_brief(eng.gs)
        doctrine, _ = eng.recommend_doctrine()
    else:
        text, opps = B.government_brief(eng)
        doctrine = None
    return Brief(faction=faction, turn=eng.turn, markdown=text, strategic_opportunities=opps,
                 recommended_doctrine=doctrine)


@mcp.tool(title="List legal actions", annotations=READ)
def list_legal_actions(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    faction: Annotated[Faction, Field(description="Whose actions to enumerate.")],
    province: Annotated[str | None, Field(description="Only actions originating here.")] = None,
    limit: Annotated[int, Field(ge=1, le=500, description="Cap on the returned list; the total is reported either way.")] = 120,
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, needed for faction='insurgent' while that seat is open (the list reveals where fighters are).")] = None,
) -> LegalActions:
    """Every action the rules engine accepts right now for a faction, at the minimum legal
    commitment (min_forces, no extra resources). Raise forces_committed above min_forces
    in submit_action if you want more weight; the validator still checks availability."""
    rec = _load(game_id)
    _require_active(rec)
    if faction == "insurgent":
        _require_insurgent_access(rec, seat_token, "listing insurgent actions")
    eng = Engine.load(rec)
    if province is not None and province not in E.ALL_PROVINCE_IDS:
        raise ToolError(f"unknown province {province!r}. Provinces: {', '.join(E.ALL_PROVINCE_IDS)}")
    acts = eng.legal_actions(faction, province)
    your_turn = eng.active_faction == faction and eng.phase == f"{faction}_action"
    return LegalActions(game_id=rec.game_id, faction=faction, turn=eng.turn, phase=eng.phase, your_turn=your_turn,
                        total=len(acts), truncated=len(acts) > limit,
                        actions=[LegalAction(**a) for a in acts[:limit]])


@mcp.tool(title="Preview an action", annotations=READ)
def preview_action(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    faction: Annotated[Faction, Field(description="Who would act.")],
    action: Action,
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, needed for insurgent previews while that seat is open.")] = None,
) -> ProbabilityBreakdown:
    """The probability breakdown for an action without rolling: base rate, every modifier
    with its explanation, the final success chance and the five outcome bands. Also
    reports whether the action is currently legal. Government previews are computed
    against the government's estimate of insurgent strength, not the true count; the
    roll itself (submit_action) uses the truth."""
    rec = _load(game_id)
    if faction == "insurgent":
        _require_insurgent_access(rec, seat_token, "an insurgent preview")
    eng = Engine.load(rec)
    a = _parse_action(faction, action)
    v = eng.validate(faction, a, fog=(faction == "government"))
    bd = eng.preview(faction, a)
    return ProbabilityBreakdown(action=action.model_dump(), base_probability=bd["base_probability"],
                                modifier_total=bd["modifier_total"], modifier_breakdown=bd["modifier_breakdown"],
                                modifier_explanations=bd["modifier_explanations"],
                                final_probability=bd["final_probability"],
                                outcome_probabilities=bd["outcome_probabilities"], validation=v.model_dump())


@mcp.tool(title="Recommend a doctrine", annotations=READ)
def recommend_doctrine(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    seat_token: Annotated[str | None, Field(description="Insurgent seat token, when that seat is open.")] = None,
) -> DoctrineAdvice:
    """The engine Red Team's own doctrine selection heuristic (survival, consolidation,
    exhaustion, opportunistic), offered as advice to the insurgent seat, not a constraint.
    It reads the true insurgent strength, so it needs the insurgent seat while that is open."""
    rec = _load(game_id)
    _require_insurgent_access(rec, seat_token, "doctrine advice")
    eng = Engine.load(rec)
    name, desc = eng.recommend_doctrine()
    preferred = {
        "AQ_NETWORK": ["propaganda", "assassination", "smuggling_operation", "ambush"],
        "ISIS_TERRITORIAL": ["territorial_seizure", "taxation", "shadow_court", "complex_attack"],
        "TALIBAN_ADAPTIVE": ["intimidation", "shadow_court", "hit_and_run", "recruitment_drive"],
    }[name]
    return DoctrineAdvice(doctrine=name, rationale=desc, description_uri=f"wargame://doctrine/{name.lower()}",  # type: ignore[arg-type]
                          preferred_actions=preferred)


# ── 4.3 acting ───────────────────────────────────────────────────────────────────────


@mcp.tool(title="Submit an action", annotations=WRITE)
def submit_action(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    faction: Annotated[Faction, Field(description="Who acts. Must be the active faction in its action phase.")],
    action: Action,
    seat_token: Annotated[str | None, Field(description="Required when this faction's seat was created 'open'.")] = None,
    rationale: Annotated[str | None, Field(description="Why this action. Stored in the log, read by the AAR and the benchmark. Name the doctrine and the alternatives you rejected.")] = None,
) -> TurnResult:
    """Validate, adjudicate (seeded d100 against the probability tables), apply mutations
    and cascades, advance two phases, recalculate forces, check victory, persist.

    An illegal action is refused with the engine's errors and up to three legal
    alternatives from the same province; the game is untouched."""
    rec = _load(game_id)
    _require_active(rec)
    S.check(rec, faction, seat_token)
    eng = Engine.load(rec)
    a = _parse_action(faction, action)
    turn = eng.turn
    try:
        res = eng.submit(faction, a, rationale)
    except WrongTurn as e:
        raise ToolError(str(e))
    except GameOver as e:
        raise ToolError(str(e))
    except IllegalAction as e:
        alts = "; ".join(
            f"{x['action_type']} from {x['source_province']}" + (f" to {x['target_province']}" if x["target_province"] else "")
            + f" (min_forces {x['min_forces']}, cost {x['cost']})" for x in e.alternatives) or "none from that province"
        raise ToolError(f"illegal action: {' | '.join(e.errors)}. Legal alternatives: {alts}. "
                        f"Call list_legal_actions for the full list.")
    if res.victory:
        rec.winner = res.victory
    rec = _commit(rec, eng, entries=[res.log_entry])
    return _turn_result(rec, eng, faction, turn, res)


@mcp.tool(title="Ask the international community", annotations=WRITE)
def request_international_community(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    request_type: Annotated[Literal["military_aid", "sanctions", "diplomatic_pressure", "humanitarian", "peacekeeping"], Field(description="What is asked for.")],
    justification: Annotated[str, Field(min_length=1, description="Why. Stored in the log.")],
    target: Annotated[str | None, Field(description="Country or actor the request concerns, if any.")] = None,
    seat_token: Annotated[str | None, Field(description="Required when the government seat is 'open'.")] = None,
) -> ICResponseView:
    """Government only, during the government action phase; does not consume the phase.

    Narrative only: the engine's IC oracle (rule-based path, seeded from the game dice so a
    replay returns the same decision) answers with a decision, conditions and a timeline, but
    the vendored engine applies no mutation for it, so it has no effect on the board. Use it to
    frame a rationale, not to change the odds."""
    rec = _load(game_id)
    _require_active(rec)
    S.check(rec, "government", seat_token)
    eng = Engine.load(rec)
    try:
        ic = eng.ic_request(request_type, justification, target)
    except (WrongTurn, GameOver) as e:
        raise ToolError(str(e))
    entry = {"kind": "ic_request", "turn": eng.turn, "faction": "government",
             "ic": {**ic, "justification": justification}}
    rec = _commit(rec, eng, entries=[entry])
    return ICResponseView(game_id=rec.game_id, version=rec.version, **ic)


@mcp.tool(title="Pass the turn", annotations=WRITE)
def pass_turn(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    faction: Annotated[Faction, Field(description="Who passes. Must be the active faction in its action phase.")],
    seat_token: Annotated[str | None, Field(description="Required when this faction's seat is 'open'.")] = None,
) -> TurnResult:
    """Decline to act this phase; the phases advance exactly as after an action."""
    rec = _load(game_id)
    _require_active(rec)
    S.check(rec, faction, seat_token)
    eng = Engine.load(rec)
    turn = eng.turn
    try:
        res = eng.pass_turn(faction)
    except (WrongTurn, GameOver) as e:
        raise ToolError(str(e))
    if res.victory:
        rec.winner = res.victory
    rec = _commit(rec, eng, entries=[res.log_entry])
    return _turn_result(rec, eng, faction, turn, res)


# ── 4.4 learning ─────────────────────────────────────────────────────────────────────


@mcp.tool(title="Turn feedback", annotations=READ)
def turn_feedback(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    turn: Annotated[int | None, Field(ge=1, description="Which turn's government action to analyse. Default: the latest.")] = None,
) -> PostTurnFeedbackView:
    """The engine's post-turn analysis (rule-based path) of a government action: what it
    did, what the Red Team will likely do, missed opportunities, doctrine, a historical parallel."""
    rec = _load(game_id)
    eng = Engine.load(rec)
    try:
        d = L.feedback(rec, eng.gs, turn)
    except LookupError as e:
        raise ToolError(str(e))
    return PostTurnFeedbackView(game_id=rec.game_id, **d)


@mcp.tool(title="After-action review", annotations=READ)
def after_action_review(game_id: Annotated[str, Field(description="Handle from new_game.")]) -> AARView:
    """The engine's AAR (rule-based path) with a 0-100 performance_score, turning points,
    doctrine compliance and lessons. Requires an ended game: call end_game first."""
    rec = _load(game_id)
    if rec.status != "ended":
        raise ToolError("after_action_review needs an ended game; call end_game first (or play to victory)")
    d = L.aar(rec, Engine.load(rec).gs)
    return AARView(game_id=rec.game_id, **d)


@mcp.tool(title="Strategy profile", annotations=READ)
def strategy_profile(game_id: Annotated[str, Field(description="Handle from new_game.")]) -> StrategyProfileView:
    """Classify the government's COIN approach (population-centric, enemy-centric,
    clear-hold-build, ink spot, attrition, hybrid) from the game log, with evidence."""
    rec = _load(game_id)
    d = L.strategy(rec)
    return StrategyProfileView(game_id=rec.game_id, approach=d["primary_approach"],
                               secondary_approach=d.get("secondary_approach"), confidence=d["confidence"],
                               approach_scores=d["approach_scores"], evolution=d["evolution"],
                               doctrinal_comparison=d["doctrinal_comparison"],
                               effectiveness_assessment=d["effectiveness_assessment"],
                               recommendations=d["recommendations"], evidence=d["supporting_evidence"],
                               historical_parallels=d["historical_parallels"],
                               government_actions_seen=d["government_actions_seen"])


@mcp.tool(title="Pattern report", annotations=READ)
def pattern_report(game_id: Annotated[str, Field(description="Handle from new_game.")]) -> PatternView:
    """The engine's pattern detector over the government's moves: favourites, geography,
    tempo, risk profile, tendencies and how predictable the player is to the Red Team."""
    rec = _load(game_id)
    return PatternView(game_id=rec.game_id, **L.patterns(rec))


# ── resources ────────────────────────────────────────────────────────────────────────


@mcp.resource("wargame://doctrine/{name}", mime_type="text/markdown")
def doctrine(name: str) -> str:
    """One of the three insurgent doctrines: aq_network, isis_territorial, taliban_adaptive."""
    path = E.DOCTRINE_DIR / f"{name.lower()}.md"
    if not path.is_file() or path.parent != E.DOCTRINE_DIR:
        raise ResourceNotFoundError(f"wargame://doctrine/{name}")
    return path.read_text()


def _rules_actions() -> dict:
    from .engine import ADJACENCY_TYPES

    requires_source_control = {E.ActionType.DEPLOY, E.ActionType.REINFORCE, E.ActionType.FORTIFY,
                               E.ActionType.SERVICE_PROVISION, E.ActionType.CHECKPOINT_NETWORK}
    out = {}
    for a in E.ActionType:
        factions = [f for f, s in (("government", E.GOVERNMENT_ACTION_TYPES), ("insurgent", E.INSURGENT_ACTION_TYPES)) if a in s]
        out[a.value] = {
            "factions": factions,
            "cost": E.rules_engine.ACTION_COSTS.get(a, 0),
            "min_forces": E.rules_engine.MIN_FORCE_REQUIREMENTS.get(a, 0),
            "target_must_be_adjacent": a in ADJACENCY_TYPES,
            "requires_source_control": a in requires_source_control,
        }
    return {
        "actions": out,
        "rules": [
            "forces_committed may not exceed the faction's forces in source_province",
            "cost + resources_spent may not exceed the faction's resources",
            "when target_must_be_adjacent and a target is given, it must be adjacent to the source (or equal to it)",
            "requires_source_control actions cannot start from a province the other side controls",
            "final success probability is base + modifiers, clamped to 5..95; roll d100, success when roll <= final",
        ],
    }


@mcp.resource("wargame://rules/actions", mime_type="application/json")
def rules_actions() -> dict:
    """The action vocabulary with costs, minimum forces, adjacency and control requirements, from the engine's constants."""
    return _rules_actions()


@mcp.resource("wargame://rules/probability-tables", mime_type="application/json")
def rules_probability_tables() -> dict:
    """The engine's probability_tables.json verbatim."""
    return json.loads(open(E.PROBABILITY_TABLES_PATH).read())


@mcp.resource("wargame://rules/map", mime_type="application/json")
def rules_map() -> dict:
    """Provinces with terrain, borders, capital flag and the adjacency graph."""
    gs = E.GameStateStore()
    gs.load_initial_state(E.INITIAL_STATE_PATH)
    return {
        "provinces": {p.id: {"name": p.name, "terrain": p.terrain.value, "is_capital": p.is_capital,
                             "borders_mali": p.borders_mali, "borders_algeria": p.borders_algeria,
                             "borders_senegal": p.borders_senegal} for p in gs.get_all_provinces()},
        "adjacency": E.rules_engine.PROVINCE_ADJACENCY,
    }


@mcp.resource("wargame://game/{game_id}", mime_type="application/json")
def game_resource(game_id: str) -> dict:
    """The state view of one game: full when no seat is open or the game has ended, otherwise
    the government (fogged) view, because a resource URI cannot carry a seat token."""
    try:
        rec = _store().get(game_id)
    except UnknownGame:
        raise ResourceNotFoundError(f"wargame://game/{game_id}")
    return _state_view(rec, Engine.load(rec), _default_view(rec, None)).model_dump(mode="json")


@mcp.resource("wargame://game/{game_id}/log", mime_type="application/json")
def game_log(game_id: str) -> dict:
    """The turn log with rationales: every action, pass and IC request, with adjudication and
    mutations. Both sides' rationales and rolls are secrets while a seat is open, so the log is
    served only when no seat is open or the game has ended."""
    try:
        rec = _store().get(game_id)
    except UnknownGame:
        raise ResourceNotFoundError(f"wargame://game/{game_id}/log")
    if rec.status == "active" and any(_open(rec, f) for f in S.FACTIONS):
        raise ResourceNotFoundError(f"wargame://game/{game_id}/log is withheld while a seat is open; "
                                    "it becomes readable when the game ends")
    return {"game_id": rec.game_id, "seed": rec.seed, "status": rec.status, "version": rec.version, "log": rec.turn_log}


# ── prompts ──────────────────────────────────────────────────────────────────────────


def _red_team_system() -> str:
    text = (E.PROMPTS_DIR / "red_team_system.md").read_text()
    # Everything before OUTPUT FORMAT is doctrine and context; the JSON contract is replaced by tools.
    return text.split("## OUTPUT FORMAT")[0].rstrip()


@mcp.prompt(title="Play the Red Team")
def play_red_team(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    doctrine: Annotated[str | None, Field(description="AQ_NETWORK | ISIS_TERRITORIAL | TALIBAN_ADAPTIVE; default: ask recommend_doctrine.")] = None,
) -> str:
    """The engine's Red Team system prompt rewritten for tool use."""
    d = (doctrine or "").upper() or None
    return (
        f"{_red_team_system()}\n\n"
        "## HOW YOU ACT (MCP tools)\n\n"
        f"You play the insurgent seat of game `{game_id}`. Each insurgent turn:\n"
        f"1. `situation_brief(game_id, faction='insurgent')` and `get_state(game_id, view='insurgent')`.\n"
        + (f"2. Your doctrine is fixed to {d}; read `wargame://doctrine/{d.lower()}`.\n" if d else
           "2. `recommend_doctrine(game_id)`; read the doctrine resource it names.\n")
        + "3. `list_legal_actions(game_id, faction='insurgent')` and choose two candidates that fit the doctrine.\n"
        "4. `preview_action` both; compare final_probability and the modifier explanations.\n"
        "5. `submit_action(game_id, faction='insurgent', action=..., rationale=...)`. The rationale names the "
        "doctrine and the alternative you rejected. If the tool refuses the action, pick one of the alternatives "
        "it lists or re-read `list_legal_actions`; never resubmit the same illegal action.\n"
        "6. Stop when `submit_action` returns a `victory`, or when it is not your turn (`next.active_faction`).\n"
        "Do not call `pass_turn` while any legal action fits the doctrine."
    )


@mcp.prompt(title="Play the Government")
def play_government(
    game_id: Annotated[str, Field(description="Handle from new_game.")],
    approach: Annotated[Literal["population_centric", "enemy_centric", "clear_hold_build", "ink_spot"] | None, Field(description="COIN approach to follow; default: your judgement.")] = None,
) -> str:
    """The mirror prompt for the government seat, with the four COIN approaches the strategy classifier recognises."""
    approaches = {
        "population_centric": "service_provision, tribal_engagement, amnesty_program, media_campaign first; kinetic action only to protect gains",
        "enemy_centric": "assault, raid, leadership_targeting, cordon_and_search against concentrations; accept sentiment cost",
        "clear_hold_build": "clear a province (assault/clear_and_hold), hold it (patrol/checkpoint_network), build (service_provision), then move on",
        "ink_spot": "secure the capital and Trarza/Inchiri first, then expand province by province along adjacency",
    }
    chosen = f"Follow the {approach} approach: {approaches[approach]}.\n" if approach else (
        "Choose and name a COIN approach (population_centric, enemy_centric, clear_hold_build, ink_spot) in your first rationale.\n")
    return (
        "# Government Command\n\n"
        "You command the Mauritanian government's response to a jihadist insurgency entering from Mali after a garrison "
        "mutiny in Hodh Ech Chargui. You win by controlling all provinces with average sentiment above 30 and insurgent "
        "forces below 500; you lose if the capital falls, the insurgents hold 7 provinces, or legitimacy drops below 20. "
        "You see only the intelligence estimate of insurgent strength, not the true count.\n\n"
        f"## HOW YOU ACT (MCP tools)\n\nYou play the government seat of game `{game_id}`. {chosen}"
        "Each government turn:\n"
        "1. `situation_brief(game_id, faction='government')` and `get_state(game_id, view='government')`.\n"
        "2. Read `wargame://rules/actions` once for costs and minimum forces.\n"
        "3. `list_legal_actions(game_id, faction='government')`; shortlist two actions that fit the approach.\n"
        "4. `preview_action` both and read the modifier explanations before committing forces.\n"
        "5. `submit_action(game_id, faction='government', action=..., rationale=...)`; the rationale names the approach "
        "and the rejected alternative. Correct a refused action from the alternatives the error lists.\n"
        "6. `request_international_community` is free and does not use your action; use it when legitimacy or the "
        "Mali border justifies it.\n"
        "7. After each turn, `turn_feedback(game_id)` tells you what the engine's analyst thought of the move."
    )


@mcp.prompt(title="After-action debrief")
def after_action_debrief(game_id: Annotated[str, Field(description="Handle of an ended game.")]) -> str:
    """Write the debrief in the engine's AAR structure from the three learning tools."""
    return (
        f"Game `{game_id}` is over (if not, call end_game). Call `after_action_review`, `strategy_profile` and "
        "`pattern_report`, then `replay` to confirm the record is reproducible. Write the debrief with these headings, "
        "in this order: Executive Summary (quote performance_score and the outcome), Strategic Assessment, Turning Points "
        "(each with turn, action, outcome, lesson), Pattern Analysis (favourite actions, predictability_score, what the "
        "Red Team could exploit), Doctrine Compliance (population-centric vs enemy-centric ratio), Lessons Learned, "
        "Improvement Areas. Quote numbers from the tools; do not invent events the log does not contain."
    )


# ── entry point ──────────────────────────────────────────────────────────────────────


def loopback_security():
    """Host/Origin allow-list for --no-auth: loopback only, any port."""
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
        allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*", "http://localhost", "http://localhost:*"],
    )


def http_settings(*, host: str, port: int, no_auth: bool, public_url: str | None, env: dict):
    """Resolve the HTTP configuration or exit with a message. Returns (server, bind_host, security).

    --no-auth always binds loopback. With auth, OAUTH_ISSUER_URL plus OAUTH_SHARED_SECRET and/or
    OAUTH_STATIC_TOKENS are required; a non-loopback bind needs --public-url (or OAUTH_RESOURCE_URL)
    so the resource URL, the Host allow-list and the Origin allow-list come from the name clients
    use rather than from the bind address."""
    from urllib.parse import urlparse

    from mcp.server.transport_security import TransportSecuritySettings

    if no_auth:
        return mcp, "127.0.0.1", loopback_security()
    issuer = env.get("OAUTH_ISSUER_URL")
    if not issuer:
        print("HTTP mode needs OAUTH_ISSUER_URL (with OAUTH_SHARED_SECRET and/or OAUTH_STATIC_TOKENS), "
              "or --no-auth for loopback.", file=sys.stderr)
        raise SystemExit(2)
    if not env.get("OAUTH_SHARED_SECRET") and not env.get("OAUTH_STATIC_TOKENS"):
        print("OAUTH_ISSUER_URL is set but neither OAUTH_SHARED_SECRET nor OAUTH_STATIC_TOKENS is; "
              "every request would be rejected. Set one, or use --no-auth.", file=sys.stderr)
        raise SystemExit(2)
    loopback = host in ("127.0.0.1", "localhost", "::1")
    public_url = public_url or env.get("OAUTH_RESOURCE_URL")
    if not public_url:
        if not loopback:
            print(f"--host {host} is not loopback; pass --public-url https://name.example/mcp (or set "
                  "OAUTH_RESOURCE_URL) so the resource URL and Host allow-list match what clients send.",
                  file=sys.stderr)
            raise SystemExit(2)
        public_url = f"http://{host}:{port}/mcp"
    parsed = urlparse(public_url)
    if not parsed.scheme or not parsed.hostname:
        print(f"--public-url must be an absolute URL, got {public_url!r}", file=sys.stderr)
        raise SystemExit(2)
    from .auth import SCOPE, JwtVerifier

    try:
        verifier = JwtVerifier.from_env(issuer, public_url, env=env)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
    server = build_server(token_verifier=verifier, auth=AuthSettingsFactory(issuer, public_url, [SCOPE]))
    _reregister(server)
    names = [parsed.hostname] + ([host] if not loopback and host != parsed.hostname and host != "0.0.0.0" else [])
    extra = [h for h in env.get("ALLOWED_HOSTS", "").split(",") if h]
    allowed_hosts = []
    for n in names + extra:
        allowed_hosts += [n, f"{n}:*"] if not n.endswith(":*") else [n]
    allowed_origins = [f"{scheme}://{n}" for n in names + extra for scheme in ("https", "http")]
    allowed_origins += [f"{scheme}://{n}:*" for n in names + extra for scheme in ("https", "http")]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts,
                                         allowed_origins=allowed_origins)
    return server, host, security


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="coin-wargame-mcp", description=__doc__.split("\n\n")[0])
    ap.add_argument("--stdio", action="store_true", help="stdio transport (default)")
    ap.add_argument("--http", action="store_true", help="Streamable HTTP transport")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (HTTP)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--public-url", default=None,
                    help="URL clients use, e.g. https://wargame.example.com/mcp; required with auth when --host is not loopback")
    ap.add_argument("--no-auth", action="store_true",
                    help="HTTP without bearer tokens; binds loopback only with Host/Origin checks")
    args = ap.parse_args(argv)
    if not args.http:
        mcp.run()
        return
    server, host, security = http_settings(host=args.host, port=args.port, no_auth=args.no_auth,
                                           public_url=args.public_url, env=dict(os.environ))
    server.run(transport="streamable-http", host=host, port=args.port, transport_security=security)


def AuthSettingsFactory(issuer: str, resource: str, scopes: list[str]):
    from mcp.server.auth.settings import AuthSettings

    return AuthSettings(issuer_url=AnyHttpUrl(issuer), resource_server_url=AnyHttpUrl(resource),
                        required_scopes=scopes, validate_token_resource=True)


def _reregister(server: MCPServer) -> None:
    """Copy this module's tools, resources and prompts onto a freshly built (authenticated) server."""
    server._tool_manager = mcp._tool_manager
    server._resource_manager = mcp._resource_manager
    server._prompt_manager = mcp._prompt_manager


def authed_server(issuer: str, resource: str, verifier) -> MCPServer:
    """A server with bearer auth that serves the same tools/resources/prompts as `mcp`."""
    from .auth import SCOPE

    s = build_server(token_verifier=verifier, auth=AuthSettingsFactory(issuer, resource, [SCOPE]))
    _reregister(s)
    return s


if __name__ == "__main__":
    main()
