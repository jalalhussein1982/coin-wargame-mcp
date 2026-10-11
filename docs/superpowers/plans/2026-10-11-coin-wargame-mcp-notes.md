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

## Independent review (read-only), 2026-10-11: findings verified and resolved

Confirmed and fixed (each with a failing test first, one commit per logical fix):

1. **Fog leak.** `_turn_result`, `new_game`, `get_province` and the game resource returned full views; the seed was in every view. Now: writes return the acting seat's view; reads default to the most the caller may see and `full`/`insurgent` need the insurgent token while that seat is open; `new_game` fogs the board for the seat the creator does not hold and withholds the seed when any seat is open; the game resource falls back to the fogged view; the seed appears only when no other host holds a seat or the game is over. `tests/test_fog.py` walks every government-facing payload for `insurgent_forces`, `insurgent_resources`, `insurgent_total` and the seed.
2. **Frozen, leaking estimate.** Replaced by the server-side model in `Engine` (`controller.estimates`, seeded noise per `(seed, turn, province)`, 70/30 lag after each insurgent phase). Used by the fogged view, the brief and government previews, which run on `Engine.fogged_store()`. Noise is derived from a string-seeded `random.Random`, not the game dice, so the roll sequence (and the committed eval answers) did not change.
3. **Seat guards.** `end_game` requires an open seat's token; `list_games` hides open-seat games without a token; the log resource is withheld while a seat is open. `replay` is *not* gated (see rejected, below); the insurgent brief, insurgent legal actions and previews, and doctrine advice are gated because they read true strength.
4. **auth.py.** Any malformed token returns `None` (list headers/claims, bad base64, missing parts); `exp` required and numeric; `aud` must equal the resource URL; constant-time static-token comparison; secrets under 32 bytes refused at construction; `validate_token_resource=True`.
5. **main().** `http_settings()` is the tested settings builder; `loopback_security()` lives in `server.py`; auth without a secret or static tokens exits with a message; a non-loopback bind needs `--public-url` (or `OAUTH_RESOURCE_URL`), from which the resource URL and Host/Origin allow-lists derive. README example rewritten and working.
6. **replay().** Every log entry records `state_hash` (state + controller + dice); replay checks it per entry, so `divergence_at_turn` is the first disagreeing entry, and compares controller and dice state at the end. Tamper tests for dice state, controller and a rewritten log action.
7. **IC request.** Government action phase only (`WrongTurn` → `ToolError`); the global `random` swap runs under a lock; description, prompt text, brief and README say it is narrative only. A private `random.Random` cannot be injected because the vendored method does `import random` inside its body.
8. **Anthropic provider.** `stop_reason` handled (`max_tokens` → not executed, retried with a doubled budget; `pause_turn` resumed; `refusal` → `Final`); `model_dump(exclude_none=True)`; `max_tokens` 4096; documented. Tested against a fake client.
9. **Tests.** Property test widened to seeds 0–40, asserts the filter rejected candidates and that a dropped candidate is invalid; a real two-client race through the server (two `Client`s, concurrent tasks) asserts exactly one success; `test_views` also asserts the former true-share numbers (857/571) are absent. README module count corrected (18).
10. **Use cases.** §1 mutations are the real dicts. The `30 → 25` then `30 → 50` pair is the engine's: `StateMutator._apply_resource_changes` reads the resource once and emits absolute `new_value`s, so the gain overwrites the cost. Documented in §1 and under engine quirks; not changed, because it is a rule of the vendored game.
11. **Packaging.** The shim already raised a clear `ImportError` naming `git submodule update --init`; README now states the project runs from a checkout. The bench no longer passes `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` to the server subprocess.
12. **Notes** on turn N+1 and "replay runs under current rules" added to the `submit_action` and `replay` descriptions, README and architecture.

Rejected, with reasons:

* **Gating `replay` behind a seat token.** `Replay` returns `reproduced`, `divergence_at_turn`, counts and (only when visible) the seed; no board information. Leaving it open lets a spectator verify the record, which is the point of the tool. The seed was the only secret in it and is now withheld while a seat is open.
* **Moving the `GEMINI_API_KEY` removal from the shim to `main()`.** The brief for this build required the removal in the shim. It is also the safer place: the engine's `ICOracle`, `PostTurnAnalyzer`, `AARGenerator` and `StrategyClassifier` read the variable at object construction in library code, and the in-process `Client(mcp)` path (tests, the bench) never runs `main()`. Documented in the README instead.
* **"Stale `tests/__pycache__` pyc".** Not tracked by git (`__pycache__/` and `*.pyc` are ignored); nothing to delete from the repository.
* **`test_views.py` should assert `ins~` absent.** `ins~` is the government brief's own estimate marker (`gov=1500, ins~831`); its presence is the fog working. The test asserts the true counts and the former true-share numbers are absent and `ins~` is present.
* **"IC consumes a dice draw."** It does, by design: the draw is what makes the decision replayable. It is now restricted to the government action phase, so the government cannot spend draws during the insurgent's phase to shift the insurgent's next roll.
