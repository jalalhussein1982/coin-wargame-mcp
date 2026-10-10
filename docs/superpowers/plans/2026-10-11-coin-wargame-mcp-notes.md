# coin-wargame-mcp — implementation notes and deviations from the plan

Recorded while implementing `2026-10-11-coin-wargame-mcp.md`. Each entry says what the plan asked for, what was done, and why.

## Deviations

1. **Server adapter written in one pass, tests added task by task.** The plan has `server.py` growing over Tasks 3–7. The adapter was written once in Task 3 (all seventeen tools, resources, prompts registered in spec order) because the tools share plumbing (`_load`, `_commit`, `_state_view`) and splitting it would have meant rewriting the same functions four times. The tests for each task were still written first and committed with that task; three defects they caught (`make_action` not accepting `LegalAction` rows, the IC oracle wrapper, the cache-hint middleware seeing a dict) were fixed in the task that owned them.

2. **Phase machine mirrored, not reused.** `SessionController.advance_turn` writes `datetime.now().isoformat()` into `global_state.turn_history`, which is part of `serialize()`. A replay could never match that. `Engine._advance_phase/_advance_turn` reproduce the controller's four-phase transitions exactly but write no wall-clock field. `SessionController` is still used for `check_victory_conditions` and `end_session` (bound to our state through its private fields). Victory is checked after the two phase advances, as the vendored API does, so an insurgent win on turn N is stamped turn N+1.

3. **IC oracle seeding.** The plan says "seed `random` with `dice.roll(10**6)` before calling". `_fallback_evaluate` does `import random` inside the method, so the module attribute cannot be shadowed; the wrapper saves `random.getstate()`, seeds the global generator from the game dice, calls, and restores the state. Nothing else in the process observes the seed.

4. **Government fog view uses a second model.** The SDK dumps tool results with `model_dump(mode="json")` and no `exclude_none`, so a `None` field appears as a `null` key. `GameStateView.provinces` is `list[ProvinceView] | list[FoggedProvinceView]`; the fogged model has no `insurgent_forces` field at all. `GlobalStateView.insurgent_resources` and `faction_forces.insurgent_total` are `null` in the government view (the keys exist; the test asserts they are null).

5. **Cache hints.** The spec's `ttlMs`/`cacheScope` are per-method in the SDK (`MCPServer(cache_hints={...})`, SEP-2549). Per-URI values for `resources/read` (1 h public for doctrine and rules, 0 ms private for game rows) are set by a `ServerMiddleware` that rewrites the handler result; the result arrives as a wire-shaped dict (`ttlMs`, `cacheScope`), which is what the middleware edits.

6. **`preview_action.final_probability` is a percent (5..95), not a fraction in [0, 1]** as the plan's test says. The engine's `get_probability_breakdown` returns percents and `outcome_probabilities` sums to 100; converting would have made the tool disagree with `adjudication.required_roll`. The test asserts `5 <= p <= 95`.

7. **Victory fixture.** The plan's "one move from capital capture" assumed `territorial_seizure` moves forces into the target. The engine's `StateMutator.apply_outcome` never relocates forces (its `generate_movement_mutations` is unused upstream); control is re-derived from the force ratio in the acting province after casualties. The fixture therefore puts 60 insurgents and 10 government troops inside the capital with control still `government`; one `ambush` flips control and ends the game.

8. **`LegalAction` rows are directly submittable.** `make_action` accepts a `LegalAction` dict and uses `min_forces` when `forces_committed` is absent. The benchmark stub relies on it.

9. **Benchmark rows.** One JSONL row per game for the seat under test (`--side red|gov`), with `opponent` recorded. `performance_score` is the AAR score for a government row and `100 - AAR` for an insurgent row (the AAR is scored from the government seat); `aar_government_score` is stored unchanged alongside. Result filenames use the local date.

10. **Auth verifier.** The "copied shape" from the sibling servers was not available; `auth.py` keeps the SDK's `TokenVerifier` protocol and accepts static demo tokens (`OAUTH_STATIC_TOKENS`) or HS256 JWTs (`OAUTH_SHARED_SECRET`) with `iss` = `OAUTH_ISSUER_URL`. Scope `wargame:play` is enforced by `AuthSettings.required_scopes`. The authenticated server is built by `authed_server()` which reuses the module-level managers, because `MCPServer` needs `token_verifier` and `auth` at construction.

11. **`pythonpath = ["."]` in pytest config.** The editable install's `.pth` was intermittently not honoured when running `pytest` as a console script in this checkout (the path contains a non-ASCII apostrophe); test imports no longer depend on it.

12. **Vendor root appended to `sys.path`, not prepended.** The engine ships a top-level `tests/` package; prepending shadowed this repo's `tests/` for any script that imported the server first.

13. **Turn-history enrichments.** `Engine._advance_turn` writes `government_action` and `control_changes` into each `turn_history` entry. The engine's `state_encoder` and `doctrine_selector` read those keys but its own controller never wrote them, so the insurgent brief's "GOVERNMENT PATTERNS" section and the stalemate check were dead upstream. Both are deterministic.

## Non-determinism found in the vendored engine (search: `random.`, `datetime.now`, `time.time`, `uuid`)

| Location | Effect | Handling |
|---|---|---|
| `src/core/session_controller.py:203` `datetime.now()` in `advance_turn` | timestamp inside serialized state | phase machine mirrored in `engine.py` (item 2) |
| `src/core/session_controller.py:101-102,410` `uuid4`, `datetime.now()` | session id / ended_at in the end summary only | not part of persisted state; summary returned once |
| `src/arbiter/ic_oracle.py:436` `random.random()` | IC decision | seeded from game dice, state restored (item 3) |
| `src/arbiter/ic_oracle.py:674` `hash(justification)` in the cache key | process-salted | cache bypassed: `_fallback_evaluate` called directly |
| `src/utils/dice.py:41` `time.time()` when seed is None | unseeded dice | server always passes a seed (`SystemRandom` when the caller omits one) |
| `src/utils/session_logger.py` `datetime.now()` | log files | never invoked by the server |

`replay().reproduced` is `True` for the scripted ten-turn seed-7 game (with an IC request), for every property-test game, and for every stub benchmark game.

## Engine quirks observed (not fixed upstream; documented)

* `StateMutator.apply_outcome` never moves forces between provinces; `deploy`/`reinforce`/`territorial_seizure` adjust casualties, sentiment, resources and control only.
* `request_military_aid` and the other diplomatic action types resolve through the probability tables with a `-50` modifier and apply no mutations; the IC oracle (`request_international_community`) is the meaningful path.
* The AAR performance score is generous: a stub government that patrols for 30 turns scores 99–100.
