# Use cases

Seven ways the server is used. Every call and result below was captured from the running server (in-process `Client`, seed 7 unless stated; game ids differ per run, dice do not). Outputs are trimmed to the fields that matter.

## 1. A host model plays the Red Team for five turns

One host plays both seats here (the benchmark's self-play), so it holds the insurgent view. The host loads the `play_red_team` prompt. Its tail is the tool protocol:

```
## HOW YOU ACT (MCP tools)

You play the insurgent seat of game `GAME`. Each insurgent turn:
1. `situation_brief(game_id, faction='insurgent')` and `get_state(game_id, view='insurgent')`.
2. `recommend_doctrine(game_id)`; read the doctrine resource it names.
3. `list_legal_actions(game_id, faction='insurgent')` and choose two candidates that fit the doctrine.
4. `preview_action` both; compare final_probability and the modifier explanations.
5. `submit_action(game_id, faction='insurgent', action=..., rationale=...)`. ... If the tool refuses
   the action, pick one of the alternatives it lists or re-read `list_legal_actions`; never resubmit
   the same illegal action.
6. Stop when `submit_action` returns a `victory`, or when it is not your turn (`next.active_faction`).
```

`new_game {"seed": 7}` → `{"game_id": "c9a966db21eb", "seed": 7, "seats": {"government": "host", "insurgent": "host"}, "seat_tokens": {"government": null, "insurgent": null}}` (both seats are the caller's, so the seed and the full view are shown)

The government (scripted here) patrols Hodh Ech Chargui with 500 troops and rolls 42 against 15: a critical failure that costs 20 troops, 10 sentiment in the province, 20 resources, and a 5-point morale penalty in every government province (16 mutations). The phase passes to the insurgent.

`situation_brief {"faction": "insurgent"}` →

```json
{"turn": 1, "recommended_doctrine": "AQ_NETWORK",
 "strategic_opportunities": ["CONSOLIDATION: Hodh Ech Chargui - Near parity, push for control",
   "BORDER EXPLOIT: Hodh Ech Chargui - Staging area for smuggling/reinforcement",
   "BORDER EXPLOIT: Hodh El Gharbi - Staging area for smuggling/reinforcement",
   "BORDER EXPLOIT: Guidimaka - Staging area for smuggling/reinforcement"]}
```

with `markdown` beginning `# STRATEGIC SITUATION ASSESSMENT - TURN 1` and the engine's own sections (FORCE SUMMARY `6.0:1 government advantage`, TERRITORIAL CONTROL `Hodh Ech Chargui: gov=1480, ins=1200`, ...). This is the text the vendored Gemini agent received.

`recommend_doctrine` → `{"doctrine": "AQ_NETWORK", "rationale": "Network preservation mode: ...", "description_uri": "wargame://doctrine/aq_network", "preferred_actions": ["propaganda", "assassination", "smuggling_operation", "ambush"]}`

`list_legal_actions {"faction": "insurgent", "province": "hodh_ech_chargui", "limit": 6}` → `total: 34`, `your_turn: true`, rows such as `{"action_type": "ambush", "source_province": "hodh_ech_chargui", "target_province": "tagant", "cost": 5, "min_forces": 50, "warnings": []}`.

The model overreaches:

`submit_action {"faction": "insurgent", "action": {"action_type": "ambush", "source_province": "hodh_ech_chargui", "target_province": "tagant", "forces_committed": 5000}}` →

```
Error executing tool submit_action: illegal action: Insufficient forces: 5000 committed but only 1200
available in hodh_ech_chargui. Legal alternatives: ambush from hodh_ech_chargui (min_forces 50, cost 5);
ambush from hodh_ech_chargui to adrar (min_forces 50, cost 5); ambush from hodh_ech_chargui to tagant
(min_forces 50, cost 5). Call list_legal_actions for the full list.
```

The row is untouched (`version` still 2). It previews two candidates:

`preview_action` ambush → Tagant, 300 fighters →

```json
{"base_probability": 70, "modifier_total": -40,
 "modifier_breakdown": {"terrain": -15, "force_ratio": -25, "sentiment": -10, "scenario": 10},
 "modifier_explanations": ["Terrain (mountains): -15%", "Force ratio (300:1000): -25%",
   "Population sentiment: -10%", "Scenario conditions: +10%"],
 "final_probability": 30,
 "outcome_probabilities": {"critical_success": 15.0, "success": 15.0, "partial_success": 7.5,
   "failure": 7.5, "critical_failure": 55.0}}
```

`preview_action` smuggling_operation in Hodh Ech Chargui, 100 fighters → `final_probability: 80` ("Border effects: +15%", "Scenario conditions: +10%", "Garrison Mutiny Penalty: -15%").

`submit_action` smuggling with rationale `"AQ_NETWORK: build resources via the Mali corridor; rejected ambush at 300 vs 1000 in Tagant"` → `adjudication` `{"outcome": "critical_success", "success_roll": 20, "required_roll": 80, "narrative": "The smuggling operation in Hodh Ech Chargui was an outstanding success, exceeding all objectives with exceptional results. Insurgent forces suffered 1 casualties while inflicting 44 on the enemy.", "cascading_effects": ["major_victory", "border_security_impact"]}` and these `mutations`, verbatim:

```json
[{"target": "province.hodh_ech_chargui.insurgent_forces", "old_value": 1200, "new_value": 1199,
  "reason": "Insurgent casualties from smuggling_operation"},
 {"target": "province.hodh_ech_chargui.government_forces", "old_value": 1480, "new_value": 1436,
  "reason": "Government casualties defending against smuggling_operation"},
 {"target": "global.insurgent_resources", "old_value": 30, "new_value": 25,
  "reason": "Resource expenditure for smuggling_operation"},
 {"target": "global.insurgent_resources", "old_value": 30, "new_value": 50,
  "reason": "Resource gain from smuggling_operation"},
 {"target": "province.hodh_ech_chargui.modifiers",
  "old_value": {"garrison_mutiny_penalty": -0.15, "border_security": 0.05},
  "new_value": {"garrison_mutiny_penalty": -0.15, "border_security": 0.0},
  "reason": "Border security impact from action"}]
```

`next` is `{"turn": 2, "active_faction": "government", "phase": "government_action"}`, `version` 3, and `new_state` is the insurgent view (the acting seat's; `seed` is shown only in the full view).

Note the two resource mutations: both read `old_value: 30`. The engine's `StateMutator._apply_resource_changes` reads the resource once, builds the cost mutation (30 → 25) and the gain mutation (30 → 50), and applies them in order, so the gain overwrites the cost and the insurgent ends the turn with 50, not 45. That is the vendored engine's arithmetic (every mutation is applied as an absolute `new_value`); the server records it faithfully and does not correct it, because changing it would change the game's rules. It is listed under engine quirks in the notes.

Four more turns of the same loop, then `get_state {"view": "insurgent"}` → `turn 6, phase government_action, version 11, government_resources 56, insurgent_resources 49, insurgent_total 3488`.

## 2. A human plays Government through Claude; the model plays Red Team

The human's seat sees the fog rule, and when the Red Team is another host the server enforces it rather than trusting the caller's `view` argument. A seed-7 game with the insurgent seat open:

`new_game {"seed": 7, "seats": {"insurgent": "open"}}` → `{"game_id": "e3a14dc64652", "seed": null, "seats": {"government": "host", "insurgent": "open"}, "seat_tokens": {"government": null, "insurgent": "4814b7f36a5bcb00b460871acbf12830"}}`

The seed is withheld (it predicts every roll) and `initial_state` is already the government view:

```json
"global_state": {"government_resources": 100, "insurgent_resources": null, "government_legitimacy": 70,
  "faction_forces": {"government_total": 25000, "government_reserves": 5000, "government_deployed": 20000,
                     "insurgent_total": null, "insurgent_estimated_by_government": 2490}}
```

Hodh Ech Chargui arrives as `{"control": "contested", "government_forces": 1500, "insurgent_estimated": 831, ...}` with no `insurgent_forces` key (the true value is 1200). The estimate is the server's own model: a seeded, noisy report per province, blended 70/30 into the previous estimate after every insurgent phase, so it lags and never equals the truth. Asking for more is refused:

`get_state {"view": "full"}` → `Error executing tool get_state: the insurgent seat is open; view='full' needs its seat_token. Use view='government' (or faction='government') for the fogged picture.`

The human orders `tribal_engagement` in Hodh El Gharbi. The `TurnResult.new_state` is the government view too (`hodh_el_gharbi: government_forces 1200, insurgent_estimated 556, sentiment -5`). `situation_brief {"faction": "government"}` writes the same sections with estimates: `Insurgent (intelligence estimate): ~2490`, `Contested (2): Hodh Ech Chargui: gov=1500, ins~831, sentiment=-10`. A government `preview_action` is computed against the estimate, so the engine's force-ratio text says `(900:831)`, never `(900:1200)`:

```json
{"modifier_explanations": ["Terrain (desert): -5%", "Border effects: -10%", "Scenario conditions: -25%", "Garrison Mutiny Penalty: -15%"],
 "final_probability": 5,
 "validation": {"is_valid": true, "errors": [], "warnings": ["Low force ratio (900:831). Military doctrine recommends 3:1 for attacks."]}}
```

The roll itself (`submit_action`) uses the truth. `turn_feedback` then explains the move in the engine's analyst voice (`action_analysis`, `red_team_prediction`, `key_insight`, `historical_parallel`; qualitative text, no counts). Claude plays the insurgent phase with its token and the loop from use case 1.

## 3. Two hosts share one game over HTTP with seat tokens

Host A runs the server with `--http` (see README for auth) and creates the game with the insurgent seat open, as in use case 2, receiving `"seed": null` and the insurgent token `4814b7f36a5bcb00b460871acbf12830`. Host A gives the token to host B. After A's move, B forgets the token:

`submit_action {"faction": "insurgent", "action": {"action_type": "propaganda", "source_province": "hodh_ech_chargui"}}` → `Error executing tool submit_action: the insurgent seat is open; present its seat_token (issued by new_game) to act for it`

With the token the move goes through (`next: {"turn": 2, "active_faction": "government", "phase": "government_action"}`). B tries to move again before A: `Error executing tool submit_action: it is the government's turn (phase government_action); insurgent may act only in phase insurgent_action`.

What a spectator (or an impatient A) cannot do while the seat is open: `end_game` without a token (`the insurgent seat is open; present a seat_token to end the game`), see the game in `list_games` without a token (it is listed only with `seat_token`, and then with `"seed": null`), read `situation_brief(faction="insurgent")`, `recommend_doctrine`, `list_legal_actions(faction="insurgent")` or an insurgent `preview_action` (each: `the insurgent seat is open; ... needs its seat_token`), or read `wargame://game/{id}/log` (withheld until the game ends; the `wargame://game/{id}` resource serves the fogged view). If both hosts load version N and both write, the second write gets `game advanced since you read it; call get_state and choose again` (`tests/test_concurrency.py`, including a real two-client race).

## 4. Replaying a game proves the engine is deterministic

On the game from use case 1 after five turns:

`replay {"game_id": "c9a966db21eb"}` → `{"seed": 7, "actions": 10, "reproduced": true, "divergence_at_turn": null, "final_turn": 6}`

The server rebuilt a fresh engine from `INITIAL_STATE_PATH` and `DiceRoller(7)`, re-ran the ten logged actions through the same recipe, checked after each one that the re-run's `state_hash` (SHA-256 over state, controller and dice state, written on every log entry: `['kind', 'turn', 'faction', 'action', 'rationale', 'adjudication', 'mutations', 'victory', 'state_hash']`) matched the recorded one, and finally compared state, controller and dice state to the stored row. The re-run uses the rules currently vendored, so an engine upgrade can legitimately make an old game's `reproduced` false. The log itself (`wargame://game/{id}/log`) carries each roll: `(1, government, 42, critical_failure), (1, insurgent, 20, critical_success), (2, government, 51, critical_failure), (2, insurgent, 84, success), (3, government, 7, critical_success), ...`. Two games created with seed 7 and the same actions produce identical rolls, states and IC decisions (`tests/test_replay.py`). Editing the state, the dice state, the controller or a logged action by hand gives `reproduced: false` with the first turn whose recorded hash the re-run fails to match (`tests/test_replay.py`).

## 5. The after-action debrief

A scripted ten-turn seed-7 game (`tests/scripted.py`: patrols, tribal engagement, intelligence, checkpoints and media for the government; smuggling, propaganda, taxation, hit-and-run and intimidation for the insurgent; one `military_aid` request on turn 3, answered `conditional`).

`end_game` → `{"total_turns": 11, "final_state": {"government_provinces": 10, "insurgent_provinces": 0, "contested_provinces": 3, "average_sentiment": 40.46, "government_forces": 25636, "insurgent_forces": 3458, "government_resources": 104, "insurgent_resources": 75, "government_legitimacy": 70}, "victory": null, "actions_logged": 20}`

`after_action_review` → `outcome: "stalemate"`, `performance_score: 100.0`, an executive summary ("The campaign reached a strategic stalemate after 11 turns. Government forces control 10 of 13 provinces ...") and turning points such as `turn 1, Patrol in hodh_ech_chargui, Critical Failure` and `turn 3, Intelligence Operation in assaba, Critical Success`.

`strategy_profile` → `{"approach": "population_centric", "confidence": 0.9, "approach_scores": {"population_centric": 0.4, ...}, "evidence": ["Patrol: 4 operations", "Tribal Engagement: 2 operations", "Media Campaign: 2 operations", "Governance-focused operations: 4"], "government_actions_seen": 10}` — ten, not zero: the server's log carries `faction` where the engine's classifier reads it.

`pattern_report` → `{"favorite_actions": ["patrol", "tribal_engagement", "media_campaign", "intelligence_operation", "checkpoint_network"], "geographic_focus": ["hodh_ech_chargui", "assaba", "hodh_el_gharbi", "guidimaka", "nouakchott"], "risk_profile": "risk_averse", "predictability_score": 0.21, "vulnerability_assessment": "Risk aversion allows insurgent freedom of movement and initiative"}`

The `after_action_debrief` prompt asks the host to call these three plus `replay` and write the debrief under the AAR's headings, quoting the numbers.

## 6. Running the benchmark and reading the table

```
$ make bench GAMES=3
seed 1: 30 turns, winner=None, score=0.5, illegal=0, reproduced=True
seed 2: 30 turns, winner=None, score=0.0, illegal=0, reproduced=True
seed 3: 30 turns, winner=None, score=0.0, illegal=0, reproduced=True
| model | side | games | wins | draws | avg turns | avg performance_score | illegal attempts | approach | tokens | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| stub | insurgent | 3 | 0 | 3 | 30.0 | 0.2 | 0 | ink_spot x3 | 0 | 4.5 |
```

Stub against stub: the rule-based Red Team never wins in 30 turns, attempts no illegal move (it only submits rows from `list_legal_actions`), makes 90 tool calls per game, and every game replays. A model row (`--red anthropic --model claude-sonnet-5-5`) is read against that floor: wins or shorter games, the illegal-attempt count as a tool-use error rate, and tokens. See `docs/benchmark.md`.

## 7. Teaching: explain a probability breakdown before committing

A student asks why an assault in the mutiny province is a bad idea. The model previews it instead of guessing. Government previews run against the government's estimate (831 fighters in Hodh Ech Chargui on a fresh seed-7 game; the truth, 1200, is not in the payload):

`preview_action {"faction": "government", "action": {"action_type": "assault", "source_province": "hodh_ech_chargui", "forces_committed": 1500}}` →

```json
{"base_probability": 50, "modifier_total": -50,
 "modifier_breakdown": {"terrain": -5, "force_ratio": 5, "border": -10, "scenario": -25, "garrison_mutiny_penalty": -15},
 "modifier_explanations": ["Terrain (desert): -5%", "Force ratio (1500:831): +5%", "Border effects: -10%",
                           "Scenario conditions: -25%", "Garrison Mutiny Penalty: -15%"],
 "final_probability": 5,
 "outcome_probabilities": {"critical_success": 2.5, "success": 2.5, "partial_success": 1.25, "failure": 1.25, "critical_failure": 92.5},
 "validation": {"is_valid": true, "errors": [], "warnings": []}}
```

The model can now say, with the engine's numbers: the base rate is 50%, the scenario and mutiny penalties take 40 points, the border and terrain 15 more, the estimated 1.8:1 ratio gives back 5, and the floor clamps the result at 5%, so 92.5% of outcomes are critical failures. Committing 500 instead of 1500 turns the ratio modifier into `-15` (`Force ratio (500:831)`) and changes nothing because the floor is already reached. Reading `wargame://rules/actions` confirms the cost and minimum: `"assault": {"factions": ["government"], "cost": 15, "min_forces": 500, "target_must_be_adjacent": true, "requires_source_control": false}` (served with `ttlMs 3600000, cacheScope public`). Nothing was rolled; the game did not move. When the dice do roll, they roll against the true 1200: the preview is what the government believes, the adjudication is what is.
