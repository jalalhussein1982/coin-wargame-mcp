# Use cases

Seven ways the server is used. Every call and result below was captured from the running server (in-process `Client`, seed 7 unless stated; game ids differ per run, dice do not). Outputs are trimmed to the fields that matter.

## 1. A host model plays the Red Team for five turns

The host loads the `play_red_team` prompt. Its tail is the tool protocol:

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

`new_game {"seed": 7}` → `{"game_id": "5491e996384c", "seed": 7, "seats": {"government": "host", "insurgent": "host"}, "seat_tokens": {"government": null, "insurgent": null}}`

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

`submit_action` smuggling with rationale `"AQ_NETWORK: build resources via the Mali corridor; rejected ambush at 300 vs 1000 in Tagant"` →

```json
{"adjudication": {"outcome": "critical_success", "success_roll": 20, "required_roll": 80,
   "narrative": "The smuggling operation in Hodh Ech Chargui was an outstanding success ... Insurgent forces suffered 1 casualties while inflicting 44 on the enemy.",
   "cascading_effects": ["major_victory", "border_security_impact"], "...": "..."},
 "mutations": ["global.insurgent_resources 30 -> 25 (cost)", "global.insurgent_resources 30 -> 50 (gain)", "..."],
 "next": {"turn": 2, "active_faction": "government", "phase": "government_action"}, "status": "active", "version": 3}
```

Four more turns of the same loop, then `get_state {"view": "insurgent"}` → `turn 6, phase government_action, version 11, government_resources 56, insurgent_resources 49, insurgent_total 3488`.

## 2. A human plays Government through Claude; the model plays Red Team

The human's seat sees the fog rule. `get_state {"view": "government"}` on a fresh seed-7 game:

```json
"global_state": {"government_resources": 100, "insurgent_resources": null, "government_legitimacy": 70,
  "faction_forces": {"government_total": 25000, "government_reserves": 5000, "government_deployed": 20000,
                     "insurgent_total": null, "insurgent_estimated_by_government": 2500}}
```

and Hodh Ech Chargui as `{"control": "contested", "government_forces": 1500, "insurgent_estimated": 857, ...}` — no `insurgent_forces` key anywhere (the true value is 1200). `situation_brief {"faction": "government"}` writes the same sections with the estimate: `Contested (2): Hodh Ech Chargui: gov=1500, ins~857, sentiment=-10`.

The human orders `tribal_engagement` in Hodh El Gharbi; it rolls 42 against 20, a critical failure. `turn_feedback` explains it in the engine's analyst voice:

```json
{"turn": 1, "outcome": "critical_failure",
 "action_analysis": "Your tribal engagement action in Hodh El Gharbi suffered a significant setback. Population-centric efforts require sustained commitment. Consider whether security conditions allow for effective delivery.",
 "red_team_prediction": "Insurgents will likely attempt to undermine governance efforts through intimidation of local officials and targeted assassinations. ...",
 "key_insight": "Governance failures provide propaganda opportunities for insurgents.",
 "historical_parallel": "Tribal engagement echoes the 'Sahwa' (Awakening) movement in Iraq (2006-2008), ..."}
```

Claude then plays the insurgent phase with the loop from use case 1, and the human reads `get_state(view="government")` again.

## 3. Two hosts share one game over HTTP with seat tokens

Host A runs the server with `--http` (see README for auth) and creates the game with the insurgent seat open:

`new_game {"seed": 21, "seats": {"insurgent": "open"}}` → `{"game_id": "afa635ca25e1", "seats": {"government": "host", "insurgent": "open"}, "seat_tokens": {"government": null, "insurgent": "ec581172164e244f684ca7ca5eaf10fd"}}`

Host A gives the token to host B. After A's patrol, B forgets the token:

`submit_action {"faction": "insurgent", "action": {"action_type": "propaganda", "source_province": "hodh_ech_chargui"}}` → `Error executing tool submit_action: the insurgent seat is open; present its seat_token (issued by new_game) to act for it`

With the token: `{"next": {"turn": 2, "active_faction": "government", "phase": "government_action"}, "version": 3, "status": "active"}`.

B tries to move again before A: `Error executing tool submit_action: it is the government's turn (phase government_action); insurgent may act only in phase insurgent_action`.

If both hosts load version N and both write, the second write gets `game advanced since you read it; call get_state and choose again` (tested in `tests/test_concurrency.py`).

## 4. Replaying a game proves the engine is deterministic

On the game from use case 1 after five turns:

`replay {"game_id": "5491e996384c"}` → `{"seed": 7, "actions": 10, "reproduced": true, "divergence_at_turn": null, "final_turn": 6}`

The server rebuilt a fresh engine from `INITIAL_STATE_PATH` and `DiceRoller(7)`, re-ran the ten logged actions through the same recipe, and compared `serialize()` to the stored row. The log itself (`wargame://game/{id}/log`) carries each roll: `(1, government, 42, critical_failure), (1, insurgent, 20, critical_success), (2, government, 51, critical_failure), (2, insurgent, 84, success), (3, government, 7, critical_success), ...`. Two games created with seed 7 and the same actions produce identical rolls, states and IC decisions (`tests/test_replay.py`). Editing a row by hand gives `reproduced: false` with the turn at which the re-run disagrees.

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

A student asks why an assault in the mutiny province is a bad idea. The model previews it instead of guessing:

`preview_action {"faction": "government", "action": {"action_type": "assault", "source_province": "hodh_ech_chargui", "forces_committed": 1500}}` →

```json
{"base_probability": 50, "modifier_total": -55,
 "modifier_breakdown": {"terrain": -5, "border": -10, "scenario": -25, "garrison_mutiny_penalty": -15},
 "modifier_explanations": ["Terrain (desert): -5%", "Border effects: -10%", "Scenario conditions: -25%", "Garrison Mutiny Penalty: -15%"],
 "final_probability": 5,
 "outcome_probabilities": {"critical_success": 2.5, "success": 2.5, "partial_success": 1.25, "failure": 1.25, "critical_failure": 92.5},
 "validation": {"is_valid": true, "errors": [], "warnings": []}}
```

The model can now say, with the engine's numbers: the base rate is 50%, the scenario and mutiny penalties take 40 points, the border and terrain 15 more, and the floor clamps it at 5%, so 92.5% of outcomes are critical failures. Committing 500 instead of 1500 adds `force_ratio: -25` and changes nothing because the floor is already reached. Reading `wargame://rules/actions` confirms the cost and minimum: `"assault": {"factions": ["government"], "cost": 15, "min_forces": 500, "target_must_be_adjacent": true, "requires_source_control": false}` (served with `ttlMs 3600000, cacheScope public`). Nothing was rolled; the game did not move.
