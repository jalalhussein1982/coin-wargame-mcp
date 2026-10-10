# coin-wargame-mcp — design

**Status:** draft for review, 2026-10-11.
**Source app:** [`jalalhussein1982/mauritania-coin-wargame`](https://github.com/jalalhussein1982/mauritania-coin-wargame) (solo COIN decision-training simulation: deterministic rules engine + arbiter, LLM Red Team, FastAPI/WebSockets, React).
**Protocol:** MCP revision 2026-07-28. **SDK:** MCP Python SDK 2.x.

## 1. What this server proves

The wargame already contains an LLM agent, the insurgent Red Team, wired to one vendor through bespoke code: a Markdown state encoder, a JSON action decoder, retry and fallback logic. This server replaces that glue with MCP, so **any host model can play either side** through tools, and the same engine can **benchmark models against each other** on identical seeded scenarios. The story for an interviewer: MCP as the agent harness, not just a data connector, plus a reproducible evaluation.

| Capability | Where |
|---|---|
| **Stateful games on a stateless transport**: server-minted game handles, persisted state, no sessions (the 2026-07-28 pattern) | §3, §7 |
| **Write tools that advance a deterministic engine**, with validation surfaced as structured errors the model can act on | §4 |
| **Structured output schemas** for a non-trivial domain model (13 provinces, 40 action types) | §4 |
| **Prompts as doctrine**: the three insurgent archetypes become MCP prompts a host model plays from | §5 |
| **A benchmark harness** that drives the server as an MCP client with a pluggable model and records after-action metrics | §9 |
| Reproducibility: seeded dice, replayable turn log, `replay` tool | §7 |

Non-goals: no change to the game's rules or probability tables; no frontend; no multiplayer beyond the two seats; no LLM calls inside the server except where the engine already has rule-based fallbacks, which the server uses (§2).

## 2. Relationship to the source app

The engine is importable without an API key and without `google-generativeai`: `GameStateStore`, `rules_engine`, `OutcomeResolver`, `StateMutator`, `CascadeEngine`, `DiceRoller`, `PatternDetector`, `StrategyClassifier` never touch Gemini, and `ICOracle`, `PostTurnAnalyzer`, `AARGenerator` degrade to rule-based paths when `GEMINI_API_KEY` is unset. The FastAPI app refuses to boot without a key, but this server does not import the API layer at all.

| Engine object | Used for |
|---|---|
| `GameStateStore.load_initial_state / serialize / deserialize / get_* / recalculate_force_totals` | state |
| `rules_engine.validate_action`, `get_valid_actions_for_faction`, `ACTION_COSTS`, `MIN_FORCE_REQUIREMENTS`, `PROVINCE_ADJACENCY` | legality |
| `OutcomeResolver.resolve`, `get_probability_breakdown`; `StateMutator.apply_outcome`; `CascadeEngine.process_cascades`; `OutcomeResult.to_adjudication_result` | adjudication |
| `SessionController` (phase/turn bookkeeping, `check_victory_conditions`) | turn lifecycle |
| `DiceRoller(seed)` | reproducibility; the server also persists `random.Random.getstate()` between calls |
| `state_encoder.encode_for_llm`, `encode_province_details` | the `situation_brief` tool, so a host model gets the same picture the Gemini agent got |
| `doctrine_selector.select_doctrine`, `get_doctrine_description` | the `recommend_doctrine` tool and the doctrine prompts |
| `PatternDetector`, `StrategyClassifier`, `AARGenerator` (rule-based path), `PostTurnAnalyzer` (rule-based path) | learning tools |
| `ICOracle` with `_fallback_evaluate` | `request_international_community` |

Pinned as `mauritania-coin-wargame @ git+https://...@<commit>`; the engine modules live under `src/`, so the dependency is installed as a package named `coin_engine` via a small shim in this repo if the upstream has no `pyproject.toml` (open decision §13).

Two engine quirks handled here, not upstream: (1) `SessionController.record_action` stores `faction` nested under `action`, while `StrategyClassifier.classify_from_turn_history` reads it at the top level, so the server records its own turn log with `faction` flattened and feeds that to the classifier; (2) the IC oracle's fallback uses unseeded `random.random()`, so the server passes decisions through a seeded wrapper to keep replays exact.

## 3. Architecture

```
MCP host (plays Government, Red Team, or both)
      │ stdio or Streamable HTTP
      ▼
coin_wargame_mcp.server         tools / resources / prompts
  ├─ games.py                   GameStore: SQLite rows keyed by game_id (handle)
  ├─ engine.py                  the adjudication recipe, legal-action enumeration, replay
  ├─ seats.py                   who may act now (phase + seat token)
  ├─ brief.py                   situation brief + probability preview
  ├─ learning.py                feedback, AAR, patterns, strategy
  └─ models.py                  Pydantic output models
      │ imports
mauritania-coin-wargame (pinned)  src.core / src.arbiter / src.agents.red_team / src.learning
      │
SQLite  games(game_id, created_at, seed, scenario, state_json, controller_json, dice_state, turn_log_json, status, seats_json, updated_at)
```

Every tool takes `game_id` as an argument. The server holds nothing in memory between requests: each call loads the row, deserialises `GameStateStore` and the controller, restores the dice state, acts, serialises back in one SQLite transaction. Optimistic concurrency: the row carries `version`; a write that finds a different version returns `ToolError("game advanced since you read it; call get_state")`. Two hosts (one per seat) can therefore share a game.

## 4. Tools

Registration order as listed. Annotations: reads `read_only_hint=True, open_world_hint=False`; writes `destructive_hint=False, open_world_hint=False` unless stated.

### 4.1 Lifecycle

| Tool | Input | Output | Notes |
|---|---|---|---|
| `new_game` | `seed?: int`, `scenario?: "mauritania_default"`, `seats?: {government: "host"\|"open", insurgent: "host"\|"open"}` | `GameCreated{game_id, seed, seat_tokens{government?, insurgent?}, initial_state: GameStateView}` | `idempotent_hint=False`. A seat marked `open` gets a token another host must present; `host` means the creating caller plays it. With both seats `host`, one model plays both sides (useful for self-play benchmarks). |
| `list_games` | `status?`, `limit` | `list[GameCard]` | `game_id, turn, active_faction, status, winner?, created_at` |
| `end_game(game_id)` | | `GameSummary` | `controller.end_session()`; `idempotent_hint=True` |
| `replay(game_id)` | | `Replay{seed, actions[], final_state}` | Re-runs the turn log from the initial state with the stored seed and asserts it reproduces `state_json`; returns `reproduced: bool`. The reproducibility proof as a tool. |

### 4.2 Reading the board (`read_only_hint=True`)

| Tool | Output | Notes |
|---|---|---|
| `get_state(game_id, view: full\|government\|insurgent)` | `GameStateView{turn, phase, active_faction, global: GlobalStateView, provinces: list[ProvinceView], victory_check?}` | `government` view shows `insurgent_estimated_by_government` rather than true insurgent totals, matching the game's fog rule; `insurgent` view sees everything the Red Team agent saw. |
| `get_province(game_id, province_id)` | `ProvinceView` + `encode_province_details` text | |
| `situation_brief(game_id, faction)` | `Brief{markdown, strategic_opportunities[]}` | `state_encoder.encode_for_llm` for the insurgent seat; a government-side encoder written in `brief.py` with the same sections for the other seat. |
| `list_legal_actions(game_id, faction, province?: str)` | `list[LegalAction{action_type, source_province, target_province?, cost, min_forces, warnings[]}]` | The engine has no legal-action enumerator; this loops `validate_action` over `get_valid_actions_for_faction(faction)` × provinces (× adjacent targets for the movement/attack types) and keeps the valid ones. Capped and paginated by province when large. |
| `preview_action(game_id, action)` | `ProbabilityBreakdown{base_probability, modifier_breakdown, modifier_explanations, final_probability, outcome_probabilities}` | `OutcomeResolver.get_probability_breakdown`; does not roll. |
| `recommend_doctrine(game_id)` | `{doctrine, rationale, description_uri}` | `doctrine_selector.select_doctrine`; the Red Team's own heuristic, offered to the host model as advice, not a constraint. |

### 4.3 Acting (write)

| Tool | Input | Output | Notes |
|---|---|---|---|
| `submit_action(game_id, faction, action: Action, seat_token?, rationale?)` | `Action{action_type, source_province, target_province?, forces_committed, resources_spent}` | `TurnResult{adjudication: AdjudicationResult, mutations[], new_state: GameStateView, victory?: VictoryResult, next: {turn, active_faction, phase}}` | Preconditions: `active_faction == faction`, phase is the faction's action phase, seat token matches when the seat is `open`. Runs the recipe: `validate_action` → `OutcomeResolver.resolve` → `StateMutator.apply_outcome` → `CascadeEngine.process_cascades` → record → advance two phases → `recalculate_force_totals` → `check_victory_conditions`. Validation failure returns `ToolError` whose message lists `errors[]` and the closest legal alternatives from `list_legal_actions`, so the model corrects itself in one step. `rationale` is stored in the turn log for the AAR and for the benchmark. |
| `request_international_community(game_id, request_type, justification, target?)` | | `ICResponse` | Government only; `ICOracle` rule-based path through the seeded wrapper. `open_world_hint=False`. |
| `pass_turn(game_id, faction, seat_token?)` | | `TurnResult` with no adjudication | For a seat that chooses not to act; advances phases. |

### 4.4 Learning (`read_only_hint=True`)

| Tool | Output | Engine path |
|---|---|---|
| `turn_feedback(game_id, turn?)` | `PostTurnFeedback` | `PostTurnAnalyzer` rule-based path |
| `after_action_review(game_id)` | `AfterActionReview` incl. `performance_score` | `AARGenerator` rule-based path; requires `status` ended |
| `strategy_profile(game_id)` | `StrategyProfile{approach, confidence, evidence}` | `StrategyClassifier` over the server's flattened turn log |
| `pattern_report(game_id)` | `PatternAnalysis` | `PatternDetector` |

### 4.5 Error policy

`ToolError` for: unknown game (names `list_games`), wrong seat or phase (states whose turn it is and the phase), invalid action (errors + alternatives), stale version, finished game. Engine exceptions are crashes: logged with the game id, generic message out.

## 5. Resources and prompts

| URI | Content | `ttlMs`/`cacheScope` |
|---|---|---|
| `wargame://doctrine/{aq_network\|isis_territorial\|taliban_adaptive}` | the doctrine Markdown files | 1 h / public |
| `wargame://rules/actions` | action vocabulary with costs, minimum forces, adjacency requirement, generated from `ACTION_COSTS`, `MIN_FORCE_REQUIREMENTS` and the validator's rules | 1 h / public |
| `wargame://rules/probability-tables` | `probability_tables.json` | 1 h / public |
| `wargame://rules/map` | provinces, terrain, borders, `PROVINCE_ADJACENCY` as JSON | 1 h / public |
| `wargame://game/{game_id}` | `GameStateView(full)` | 0 / private |
| `wargame://game/{game_id}/log` | the turn log with rationales | 0 / private |

Prompts:

- `play_red_team(game_id, doctrine?)` — the Red Team system prompt (`prompts/red_team_system.md`) and turn template rewritten for tool use: read `situation_brief(insurgent)`, read the doctrine resource, choose among `list_legal_actions`, `preview_action` the top two, `submit_action` with a `rationale` that names the doctrine and alternatives considered. The required JSON of the old decoder becomes the tool's input schema.
- `play_government(game_id, approach?: population_centric\|enemy_centric\|clear_hold_build\|ink_spot)` — the mirror for the other seat, approach names from `COINApproach`.
- `after_action_debrief(game_id)` — call `after_action_review`, `strategy_profile`, `pattern_report`, then write the debrief in the game's AAR structure.

## 6. Authorization

Default transport is stdio; the game has no personal data and the demo is local. Streamable HTTP is supported for the benchmark and for two hosts sharing a game; in that mode the server runs with the same bearer-token verifier shape as the other two servers (`TokenVerifier` + `AuthSettings`, scope `wargame:play`), and seat tokens handle who may move. A `--no-auth` flag binds to loopback only, with Host and Origin validation, for the Inspector.

## 7. State and reproducibility

- `games` row holds `state_json = GameStateStore.serialize()`, `controller_json` (session state, turn, phase, history), `dice_state` (`random.Random.getstate()` pickled to JSON-safe form), `turn_log_json` (our log: `turn, faction, action, rationale, adjudication, mutations, victory`), `seed`, `version`.
- `replay` reconstructs from `seed` and the log and compares to `state_json`; a mismatch is reported, not hidden, because it would mean the engine has a non-seeded random path (the IC fallback is the known one, wrapped).
- Completed games are kept; `GAME_TTL_DAYS` (default 30) prunes abandoned ones.
- `request_state` is not used: there is no destructive action here that needs human confirmation (ending a game is reversible by starting another with the same seed).

## 8. Testing

In-memory through `Client(mcp)` with a temporary SQLite file.

1. Protocol: tool order fixed; output schemas present; annotations as in §4; resources carry `ttlMs`/`cacheScope`.
2. `new_game(seed=42)` twice → identical `initial_state`; `seat_tokens` only for `open` seats.
3. `list_legal_actions` never returns an action `validate_action` rejects (property test over seeds and turns).
4. `submit_action` with an illegal action → `ToolError` listing errors and at least one legal alternative; state unchanged (version unchanged).
5. Seat and phase guards: insurgent acting on the government phase → `ToolError`; wrong seat token → `ToolError`.
6. A full scripted 10-turn game with seed 7 → `replay` reports `reproduced=True`; `after_action_review` returns a `performance_score`; `strategy_profile` sees the government actions (the flattened-faction fix).
7. Optimistic concurrency: two clients load the same version; the second write fails with the stale-version error.
8. `get_state(view=government)` hides true insurgent forces; `view=insurgent` shows them.
9. Victory: a fixture state one move from `capital captured` → `submit_action` returns `victory` and the game ends.
10. HTTP smoke with `--no-auth` on loopback: `tools/list` works; a non-loopback Host header is refused.

Target: 50–60 tests under 10 s.

## 9. Benchmark harness (`bench/`)

A separate console script in this repo, `coin-bench`, that is an **MCP client**:

- Connects to the server (stdio by default), creates N games with fixed seeds, and for each turn asks a model to pick the next tool call given the `play_red_team` or `play_government` prompt plus the tool list, executes the call, loops until victory or `MAX_TURNS`.
- Model access through one interface, `bench/providers.py`, with an Anthropic Messages API implementation first (tool-use loop) and a stub provider that plays `recommend_doctrine`'s rule-based policy as the baseline. Other providers are additions, not design changes.
- Records per game: seed, model, side, turns, winner, `performance_score`, `strategy_profile.approach`, tokens, illegal-action attempts (how often `submit_action` returned the validation error), and wall time. Output: `bench/results/<date>-<model>.jsonl` and a Markdown table the README embeds.
- The headline question the harness answers: on the same seeds, does the host model beat the rule-based Red Team baseline, and how often does it attempt illegal moves? That is an evaluation of tool use, which is the point of the repo.

The harness never imports the engine; it only speaks MCP. That keeps it honest as a measure of the server's interface.

## 10. Deployment and demo

- `uv run python -m coin_wargame_mcp.server` for Claude Code (`.mcp.json`); `make inspector`; `make bench MODEL=... GAMES=5`.
- A short recording in the README: a host model playing the Red Team against a scripted government for five turns, ending with `after_action_review`.
- No hosted deployment is required; Streamable HTTP mode is documented for two-host play.

## 11. Repository layout

```
coin-wargame-mcp/
  README.md
  pyproject.toml              mcp[cli], pydantic; dev: pytest, anyio, hypothesis; bench: anthropic
  .mcp.json
  coin_wargame_mcp/
    server.py games.py engine.py seats.py brief.py learning.py models.py auth.py
  bench/
    __main__.py providers.py scoring.py results/
  tests/  test_protocol.py test_lifecycle.py test_legal_actions.py test_submit.py test_seats.py
          test_replay.py test_concurrency.py test_views.py test_victory.py test_http.py
  evals/coin-wargame.xml      10 read-only QA pairs over a fixed seed-7 game log
  docs/superpowers/specs/     this document
```

## 12. Build order

1. Skeleton, `GameStore`, `new_game`/`get_state`/`list_games`, protocol tests.
2. `list_legal_actions` with the property test; `preview_action`.
3. `submit_action` recipe, seat/phase guards, versioning, victory.
4. `replay`, dice-state persistence, the seeded IC wrapper.
5. Learning tools with the flattened log; views; resources; prompts.
6. Benchmark harness with the stub provider, then the Anthropic provider; first results table.
7. HTTP mode, auth, README, evals.

## 13. Open decisions for the owner

- Packaging the upstream engine: add a `pyproject.toml` to `mauritania-coin-wargame` (one small upstream commit) or vendor a shim here. The spec prefers the upstream commit.
- Whether the benchmark's first provider is the Anthropic API (this spec) or a local model.
- Whether `end_game` should also compute and store the AAR so `after_action_review` is a pure read afterwards (this spec: yes).
