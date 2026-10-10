# Architecture

```
MCP host (Claude Code, Claude Desktop, Inspector, the bench)        plays Government, Red Team, or both
      │  stdio (default)  or  Streamable HTTP (--http [--no-auth])
      ▼
coin_wargame_mcp/server.py     thin MCP adapter: 17 tools in spec order, 6 resources, 3 prompts
  ├─ games.py                  GameStore: one SQLite row per game, compare-and-swap on `version`
  ├─ engine.py                 Engine: adjudication recipe, legal-action enumeration, phase machine, replay
  ├─ seats.py                  seat tokens for `open` seats
  ├─ brief.py                  situation briefs (engine encoder for the insurgent, fogged writer for the government)
  ├─ learning.py               feedback / AAR / strategy / patterns over the server's log
  ├─ auth.py                   TokenVerifier for HTTP mode (static tokens, HS256 JWT)
  ├─ models.py                 Pydantic output models = tool output schemas
  └─ _engine.py                sys.path shim; drops GEMINI_API_KEY; re-exports engine objects
      │ imports `src.*`
vendor/mauritania-coin-wargame (git submodule, never modified)
      src.core (GameStateStore, rules_engine, SessionController)  src.arbiter (OutcomeResolver, StateMutator,
      CascadeEngine, ICOracle)  src.agents.red_team (state_encoder, doctrine_selector)  src.learning
      │
SQLite  games(game_id, created_at, updated_at, seed, scenario, state_json, controller_json, dice_state,
              turn_log_json, status, seats_json, seat_tokens_json, winner_json, version)
```

## One request

1. The tool receives `game_id`, loads the row (`GameStore.get`), and rebuilds an `Engine`: `GameStateStore.deserialize(state_json)`, the phase dict, and a `DiceRoller(seed)` whose `random.Random` gets `setstate(dice_state)`.
2. The engine acts. For `submit_action`: `validate_action` → `OutcomeResolver.resolve` (one `d100` from the game dice) → `StateMutator.apply_outcome` → `CascadeEngine.process_cascades` (applied to the store) → record the action in this turn → advance two phases → `recalculate_force_totals` → `check_victory_conditions`.
3. `Engine.snapshot()` returns the new `serialize()`, phase dict and dice state; `GameStore.save(rec, expected_version)` writes them in one `UPDATE ... WHERE version = ?`. Zero rows updated means another writer landed first and the model is told to `get_state` again.

Nothing lives in process memory between requests. Two hosts (one per seat, over HTTP) can share a game; seat tokens decide who may move which faction, the version decides who wrote first.

## Determinism

Every random draw goes through the game's `DiceRoller`, whose full `random.Random` state is persisted after each call. Two engine paths are not seeded upstream and are wrapped here: the controller's `datetime.now()` in `turn_history` (the phase machine is mirrored without it) and the IC oracle's module-level `random.random()` (seeded from the game dice for the duration of the call). `replay(game_id)` rebuilds the game from the seed and the log and compares `serialize()` with the stored row; a mismatch is reported with the turn at which it appears.

## The log

The server keeps its own `turn_log_json`: entries of kind `action`, `pass`, `ic_request`, `end`, each with `turn`, `faction` at the top level, the action, the rationale, the adjudication (outcome, rolls, narrative, mutations) and any victory. `learning.engine_history` regroups it into the shape the engine's learning classes read (`[{turn, actions: [{faction, action, outcome: {outcome, mutations}}]}]`). The engine's own controller nests `faction` inside `action`, which is why its `StrategyClassifier` never saw a government action upstream; here it sees all of them.

## Fog

`get_state(view="government")` returns `FoggedProvinceView` rows: no `insurgent_forces` field; `insurgent_estimated` is the government's `insurgent_estimated_by_government` apportioned by province share. `insurgent_resources` and `faction_forces.insurgent_total` are null. `view="insurgent"` and `view="full"` are complete. `situation_brief(faction="government")` follows the same rule; the insurgent brief is `state_encoder.encode_for_llm` verbatim, so a host model sees exactly what the vendored Gemini Red Team saw.

## Transport and auth

stdio by default. `--http --no-auth` binds `127.0.0.1` with `TransportSecuritySettings` (DNS-rebinding protection: a foreign `Host` gets 421, a foreign `Origin` 403). With `OAUTH_ISSUER_URL` set, `--http` requires a bearer token with scope `wargame:play`; unauthenticated requests get 401 with `WWW-Authenticate: ... resource_metadata=".../.well-known/oauth-protected-resource/mcp"`. `auth.JwtVerifier` accepts `OAUTH_STATIC_TOKENS` (JSON map, for demos) or HS256 JWTs signed with `OAUTH_SHARED_SECRET`.

## Caching (protocol 2026-07-28)

`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`: `ttlMs` 3 600 000, `cacheScope` public. `resources/read`: doctrine and rules 1 h public, `wargame://game/...` 0 ms private, set per URI by a server middleware.
