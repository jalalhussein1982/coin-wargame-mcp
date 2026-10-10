# coin-wargame-mcp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An MCP server (protocol 2026-07-28, Python SDK 2.x) over the Mauritania COIN wargame engine: any host model plays Government or Red Team through tools; games persist as server-minted handles with seeded, replayable dice; a benchmark harness that only speaks MCP compares models.

**Architecture:** `games.py` persists each game as one SQLite row (`state_json`, `controller_json`, `dice_state`, `turn_log_json`, `version`). `engine.py` wraps the engine's adjudication recipe (`validate_action → OutcomeResolver.resolve → StateMutator.apply_outcome → CascadeEngine.process_cascades → record → advance phases → recalc → victory`) and enumerates legal actions. `server.py` is the thin MCP adapter. `bench/` is an MCP client with pluggable model providers.

**Tech Stack:** Python 3.12, `uv`, `mcp[cli]>=2.2`, `pydantic`, `hypothesis` (dev), `anthropic` (bench extra). Engine pinned as git submodule `vendor/mauritania-coin-wargame` (no `pyproject.toml` upstream; modules import as `src.*`).

**Spec:** `docs/superpowers/specs/2026-10-11-coin-wargame-mcp-design.md`

## Global Constraints

- Protocol 2026-07-28; `mcp>=2.2.0`; Python `>=3.12`.
- Never modify `vendor/mauritania-coin-wargame`. Engine quirks are handled in this repo (`faction` flattening; seeded IC wrapper).
- No LLM calls inside the server. `GEMINI_API_KEY` is explicitly unset in the server process so the engine's rule-based paths run.
- Every tool takes `game_id`; the server keeps no per-game state in memory between requests.
- `replay(game_id).reproduced` must be `True` for every game created by the server; a test plays 10 turns and asserts it.
- Tools in spec §4 order; every tool returns a Pydantic model.
- Commit after every task with the Co-Authored-By trailer from the session.

## Review Focus

1. `submit_action` with `forces_committed` greater than the source province's forces for the faction: `ToolError` with the engine's error text and alternatives, state untouched. Test in Task 5.
2. `new_game(seats={"insurgent":"open"})` and then an insurgent `submit_action` without `seat_token`: refused with a message naming the seat. Test in Task 6.
3. `get_state(view="government")` must not expose `insurgent_forces` per province beyond what the engine's government estimate rule allows; check the field is absent or replaced by `insurgent_estimated`. Test in Task 7.
4. `list_legal_actions` on a finished game: `ToolError("game is over")`, not an empty list. Test in Task 4.
5. Concurrent writes from two clients on the same version: exactly one succeeds. Test in Task 5.

---

## File structure

```
coin-wargame-mcp/
  pyproject.toml, .python-version, Makefile, .mcp.json
  vendor/mauritania-coin-wargame/     git submodule, pinned
  coin_wargame_mcp/
    __init__.py
    _engine.py     sys.path shim → exposes `src.core`, `src.arbiter`, `src.agents.red_team`, `src.learning`, `src.utils.dice`, `src.schemas`
    models.py      GameCreated, GameCard, GameSummary, Replay, GameStateView, GlobalStateView, ProvinceView, Brief, LegalAction,
                   ProbabilityBreakdown, DoctrineAdvice, Action, TurnResult, ICResponseView, PostTurnFeedbackView, AARView,
                   StrategyProfileView, PatternView
    games.py       GameStore(sqlite path): create/get/save(version check)/list/end/prune; GameRecord dataclass
    engine.py      Engine(game_record): load store+controller+dice; legal_actions(faction, province?); preview(action);
                   submit(faction, action, rationale); pass_turn(faction); ic_request(...); replay(); snapshot()
    seats.py       seat tokens: issue(), check(game, faction, token)
    brief.py       insurgent brief via state_encoder.encode_for_llm; government brief writer with the same sections
    learning.py    feedback/aar/strategy/patterns over the server's flattened turn log
    auth.py        JwtVerifier (copied shape)
    server.py      MCPServer wiring, tools in order, resources, prompts, main()
  bench/__init__.py, __main__.py, providers.py (Provider protocol, StubProvider, AnthropicProvider), scoring.py, results/.gitkeep
  tests/conftest.py, test_protocol.py, test_lifecycle.py, test_legal_actions.py, test_submit.py, test_seats.py,
        test_replay.py, test_concurrency.py, test_views.py, test_victory.py, test_learning.py, test_http.py, test_bench_stub.py
  evals/coin-wargame.xml
  docs/architecture.md, docs/use-cases.md, docs/benchmark.md
```

---

### Task 1: Skeleton, submodule, engine shim

**Files:** Create `pyproject.toml`, `.python-version`, `Makefile`, `coin_wargame_mcp/__init__.py`, `coin_wargame_mcp/_engine.py`, `tests/conftest.py`, `tests/test_engine_shim.py`. Add submodule `vendor/mauritania-coin-wargame`.

`_engine.py`: inserts `vendor/mauritania-coin-wargame` on `sys.path`, pops `GEMINI_API_KEY` from `os.environ`, imports and re-exports `GameStateStore`, `rules_engine`, `OutcomeResolver`, `StateMutator`, `CascadeEngine`, `DiceRoller`, `SessionController`, `TurnPhase`, `state_encoder`, `doctrine_selector`, `ICOracle`, `PostTurnAnalyzer`, `AARGenerator`, `PatternDetector`, `StrategyClassifier`, `Action/GovernmentAction/InsurgentAction/ActionType`, `INITIAL_STATE_PATH`, `PROBABILITY_TABLES_PATH`, `DOCTRINE_DIR`, `PROMPTS_DIR`. Dependencies copied from the engine's `requirements.txt` minus `google-generativeai` and web-server packages.

- [ ] Failing test: `from coin_wargame_mcp import _engine as E`; `gs = E.GameStateStore(); gs.load_initial_state(E.INITIAL_STATE_PATH)`; `len(gs.get_all_provinces()) == 13`; `"GEMINI_API_KEY" not in os.environ`.
- [ ] Implement; commit `chore: skeleton, engine submodule, shim`.

### Task 2: `GameStore` persistence

**Files:** Create `games.py`, `tests/test_games.py`.

```python
@dataclass
class GameRecord: game_id: str; seed: int; scenario: str; state: dict; controller: dict; dice_state: list; turn_log: list[dict]; status: str; seats: dict; version: int; created_at: str; updated_at: str; winner: dict | None
class GameStore:
    def __init__(self, path: Path)
    def create(self, seed, scenario, state, controller, dice_state, seats) -> GameRecord
    def get(self, game_id) -> GameRecord            # raises UnknownGame
    def save(self, rec: GameRecord, expected_version: int) -> GameRecord   # raises StaleVersion; bumps version
    def list(self, status: str | None, limit: int) -> list[GameRecord]
    def prune(self, older_than_days: int) -> int
```
`dice_state` is `random.Random.getstate()` converted to JSON-safe lists and back.

- [ ] Failing tests: round-trip of a serialized `GameStateStore`; `save` with stale version raises; dice state round-trip produces the same next `roll(100)`.
- [ ] Implement; commit `feat: game persistence with optimistic versioning`.

### Task 3: `Engine` load/snapshot and lifecycle tools

**Files:** Create `engine.py` (load/snapshot only), `models.py` (lifecycle + view models), `seats.py`, `server.py` (tools `new_game, list_games, end_game` and placeholders for the rest in spec order), `tests/test_protocol.py`, `tests/test_lifecycle.py`.

Tool order: `new_game, list_games, end_game, replay, get_state, get_province, situation_brief, list_legal_actions, preview_action, recommend_doctrine, submit_action, request_international_community, pass_turn, turn_feedback, after_action_review, strategy_profile, pattern_report`.

`Engine.load(rec) -> Engine` rebuilds `GameStateStore.deserialize(rec.state)`, a `SessionController` whose fields are restored from `rec.controller` (turn, phase, session_state, history), and a `DiceRoller(seed)` whose internal `random.Random` gets `setstate(rec.dice_state)`. `Engine.snapshot() -> (state, controller, dice_state)`.

- [ ] Failing tests: exact tool order and output schemas; `new_game(seed=42)` twice → identical `initial_state`; seat tokens only for `open` seats; `end_game` sets `status=ended` and returns the summary; `list_games` shows it.
- [ ] Implement; commit `feat: lifecycle tools and engine load/snapshot`.

### Task 4: Legal actions, preview, doctrine advice, state views

**Files:** Modify `engine.py` (`legal_actions`, `preview`), `brief.py`, `server.py`; create `tests/test_legal_actions.py`, `tests/test_views.py`.

`legal_actions(faction, province=None) -> list[dict]`: for each `ActionType` in `rules_engine.get_valid_actions_for_faction(faction)`, for each source province (filtered by `province`), targets = `[None]` for non-movement types and `PROVINCE_ADJACENCY[source]` for the types the validator requires adjacency for; build `Action(action_type, source_province, target_province, forces_committed=MIN_FORCE_REQUIREMENTS.get(type, 0), resources_spent=ACTION_COSTS[type])`; keep where `validate_action(...).is_valid`; include `warnings`. Finished game → `ToolError("game is over")` (Review Focus 4).
Views: `GameStateView` built from `serialize()`; `view="government"` replaces per-province `insurgent_forces` with `insurgent_estimated` (`insurgent_estimated_by_government` scaled by province share) and hides `insurgent_resources`; `view="insurgent"` is full.

- [ ] Failing tests: hypothesis property over seeds 0..20 → every returned action validates; `preview_action` returns `final_probability` in `[0,1]`; `recommend_doctrine` returns one of the three names; `situation_brief(insurgent).markdown` starts with `# STRATEGIC SITUATION ASSESSMENT`; government view has no `insurgent_forces` key (Review Focus 3); game over → `ToolError`.
- [ ] Implement; commit `feat: legal actions, preview, doctrine advice, state views`.

### Task 5: `submit_action` recipe, `pass_turn`, concurrency

**Files:** Modify `engine.py` (`submit`, `pass_turn`), `server.py`; create `tests/test_submit.py`, `tests/test_concurrency.py`.

`submit(faction, action, rationale)`: guard `active_faction == faction` and phase ∈ {`government_action`, `insurgent_action`} for the faction; `validate_action` → on invalid raise `IllegalAction(errors, alternatives=legal_actions(faction, action.source_province)[:3])`; `res = OutcomeResolver().resolve(action, gs, dice)`; `muts = StateMutator().apply_outcome(res, gs)`; `muts += CascadeEngine().process_cascades(res.cascading_effects, action.source_province, res.outcome, faction=="government", gs)`; append to the server's turn log `{turn, faction, action, rationale, adjudication, mutations, victory}` with `faction` at top level; `controller.record_action(...)`; `advance_phase()` twice; `recalculate_force_totals()`; `victory = controller.check_victory_conditions()`; if victory → status `ended`, `winner`. Return `TurnResult`.
Server: load rec → `Engine.load` → act → `store.save(rec', expected_version=rec.version)`; `StaleVersion` → `ToolError("game advanced since you read it; call get_state")`.

- [ ] Failing tests: a legal government `patrol` → `adjudication.outcome` set, phase advanced to `insurgent_action`, version +1; illegal (too many forces) → `ToolError` with engine error text and ≥1 alternative, version unchanged (Review Focus 1); insurgent acting on government phase → `ToolError`; `pass_turn` advances phases; two clients racing on one version → exactly one succeeds (Review Focus 5).
- [ ] Implement; commit `feat: submit_action adjudication, pass_turn, optimistic concurrency`.

### Task 6: Seats, IC request, replay

**Files:** Modify `seats.py`, `engine.py` (`ic_request`, `replay`), `server.py`; create `tests/test_seats.py`, `tests/test_replay.py`.

Seats: `seats.issue() -> str` (32 hex); `check(rec, faction, token)` → `ToolError(f"the {faction} seat is open; present its seat_token")` when the seat is `open` and the token is missing or wrong (Review Focus 2). IC request: `ICOracle()._fallback_evaluate` via a wrapper that seeds `random` with `dice.roll(10**6)` before calling, so replays agree. `replay()`: fresh `GameStateStore` from `INITIAL_STATE_PATH`, `DiceRoller(seed)`, re-run the turn log's actions through the same recipe, compare `serialize()` to `rec.state` → `Replay{reproduced, divergence_at_turn?}`.

- [ ] Failing tests: open seat refuses without token, accepts with it; a scripted 10-turn game (seed 7, fixed action list for both sides including one IC request) → `replay().reproduced is True`; tamper `rec.state` → `reproduced False` with `divergence_at_turn`.
- [ ] Implement; commit `feat: seat tokens, IC oracle, replay`.

### Task 7: Learning tools, victory, resources, prompts

**Files:** Create `learning.py`, `tests/test_learning.py`, `tests/test_victory.py`, `tests/test_resources.py`; modify `server.py`.

`learning.py`: `feedback(rec, turn)` via `PostTurnAnalyzer` rule path (sync wrapper over its async method); `aar(rec)` via `AARGenerator` rule path, requires `status=ended`; `strategy(rec)` via `StrategyClassifier.classify_from_turn_history(flattened_log)`; `patterns(rec)` via `PatternDetector`. Resources per spec §5 (`wargame://doctrine/{name}`, `wargame://rules/actions` generated from engine constants, `wargame://rules/probability-tables`, `wargame://rules/map`, `wargame://game/{id}`, `wargame://game/{id}/log`) with `ttlMs`/`cacheScope`. Prompts `play_red_team(game_id, doctrine?)`, `play_government(game_id, approach?)`, `after_action_debrief(game_id)` built from the engine's `prompts/red_team_system.md` text plus tool instructions.

- [ ] Failing tests: strategy on the seed-7 game sees >0 government actions (the flattening fix); `after_action_review` before end → `ToolError`; after `end_game` → `performance_score` present; fixture state one move from capital capture → `submit_action` returns `victory` and `status=ended`; resources list the three doctrines; `play_red_team` prompt mentions `list_legal_actions`, `preview_action`, `submit_action`.
- [ ] Implement; commit `feat: learning tools, victory, resources, prompts`.

### Task 8: Benchmark harness

**Files:** Create `bench/__init__.py`, `bench/__main__.py`, `bench/providers.py`, `bench/scoring.py`, `docs/benchmark.md`, `tests/test_bench_stub.py`.

`Provider` protocol: `choose(messages: list[dict], tools: list[dict]) -> ToolCall{name, arguments} | Final{text}`. `StubProvider`: deterministic policy using `recommend_doctrine` + first legal action of a preferred type. `AnthropicProvider(model)`: Messages API tool-use loop (read the `claude-api` skill before writing it; default model `claude-sonnet-5-5`). Runner: connects with the SDK `Client` over stdio to this server, `new_game(seed)`, alternates seats (`--red model --gov stub` by default), caps `MAX_TURNS=30`, records per game `{seed, model, side, turns, winner, performance_score, approach, illegal_attempts, tokens, seconds}` to `bench/results/<date>-<model>.jsonl`; `scoring.summarize(path) -> Markdown table`.

- [ ] Failing test: `StubProvider` vs `StubProvider` on seeds 1–3 completes without exceptions, writes 3 JSONL rows, `illegal_attempts == 0`, and each game's `replay().reproduced` is `True`.
- [ ] Implement; `make bench GAMES=3` with the stub; commit `feat: MCP-client benchmark harness`.

### Task 9: HTTP mode and auth

**Files:** Create `auth.py`, `tests/test_http.py`, `tests/test_auth.py`; modify `server.py` (`--stdio`, `--http`, `--no-auth` loopback only with Host/Origin checks; `AuthSettings` with scope `wargame:play` when `OAUTH_ISSUER_URL` set).

- [ ] Failing tests: `--no-auth` on loopback → `tools/list` works; non-loopback `Host` → 4xx; with auth: no token → 401 with `resource_metadata`.
- [ ] Implement; commit `feat: HTTP mode with optional OAuth`.

### Task 10: Documentation, use cases, evals

**Files:** Rewrite `README.md`; create `docs/architecture.md`, `docs/use-cases.md`, `evals/coin-wargame.xml`, `.mcp.json`.

`docs/use-cases.md`: (1) a host model plays the Red Team for five turns from the `play_red_team` prompt, with the tool calls and one illegal attempt corrected via the alternatives; (2) a human plays Government through Claude while the model plays Red Team; (3) two hosts share one game with seat tokens over HTTP; (4) replaying a game to prove the engine is deterministic; (5) the after-action debrief; (6) running the benchmark and reading the table; (7) teaching use: asking the model to explain a probability breakdown before committing.
`README.md`: what it proves, run, host configs, tool table, the benchmark table from Task 8's stub run, design decisions (stateless handles, seeded dice, no LLM in the server, engine quirks handled here), test counts.

- [ ] Write; `uv run pytest -q`; commit `docs: README, architecture, use cases, benchmark, evals`.

---

## Self-review notes

Spec coverage: §4.1 → Tasks 3, 6; §4.2 → Task 4; §4.3 → Tasks 5–6; §4.4 → Task 7; §5 → Task 7; §6 → Task 9; §7 → Tasks 2, 6; §8 → all; §9 → Task 8; §10–11 → Task 10. Names consistent: `GameStore.create/get/save/list`, `Engine.load/snapshot/legal_actions/preview/submit/pass_turn/ic_request/replay`, `seats.issue/check`. Review Focus pinned to Tasks 5, 6, 4, 4, 5.
