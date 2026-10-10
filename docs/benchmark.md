# Benchmark

`bench/` is an MCP client. It never imports the engine; everything it learns about a game comes through the server's tools, which is what makes it a measure of the server's interface and of a model's tool use rather than of the engine.

## What it does

For each seed it calls `new_game(seed)`, then alternates seats. The active seat's provider is given the seat's play prompt (`play_red_team` or `play_government`, fetched from the server) as the system prompt, a short JSON opener naming the game, faction and turn, the tool list, and the results of its previous calls. It answers with one tool call at a time; the runner executes it over MCP and appends the result. A turn ends when `submit_action` or `pass_turn` succeeds for that faction. A seat that has not acted after twelve calls is passed for and the stall is counted. The game ends on victory or after `--max-turns` (default 30), then the runner calls `after_action_review`, `strategy_profile` and `replay`.

One JSONL row per game describes the seat under test (`--side red` by default):

| field | meaning |
|---|---|
| `seed`, `game_id`, `turns`, `winner`, `condition` | the game |
| `model`, `side`, `opponent` | who played what |
| `performance_score` | AAR score for a government row; `100 - AAR` for an insurgent row (the AAR is scored from the government seat) |
| `aar_government_score` | the AAR score unchanged |
| `approach` | `strategy_profile.approach` for the government's play in this game |
| `illegal_attempts` | `submit_action` calls the server refused as illegal (the tool-use error rate) |
| `tool_calls`, `stalls` | how many calls the seat made, how often it failed to act within twelve |
| `reproduced` | `replay(game_id).reproduced` |
| `tokens`, `seconds` | provider usage and wall time |

`bench.scoring.summarize(path)` prints the Markdown table the README embeds.

## Providers

* `stub` — the rule-based baseline. Insurgent: `recommend_doctrine`, then `list_legal_actions`, then the first legal action among the doctrine's preferred types (AQ_NETWORK: propaganda, smuggling, ambush, assassination, taxation; ISIS_TERRITORIAL: seizure, taxation, shadow court, complex attack; TALIBAN_ADAPTIVE: intimidation, shadow court, hit-and-run, recruitment). Government: patrol, tribal engagement, service provision, checkpoint network, intelligence operation, media campaign. It passes when nothing is affordable. Three calls per turn, deterministic, no key needed.
* `anthropic` — a Messages API tool-use loop (`claude-sonnet-5-5` by default, `--model` to change). Tools are the MCP tool list converted to `{name, description, input_schema}`; `tool_choice` is `auto` with parallel calls disabled so the runner executes one call at a time. Importable without a key; the first `choose` without `ANTHROPIC_API_KEY` raises.

## Running

```
make bench GAMES=3                                   # stub vs stub, no key
uv run python -m bench --games 5 --seed 100 --red anthropic --gov stub --model claude-sonnet-5-5
uv run python -m bench --games 5 --red stub --gov anthropic --side gov
```

Results land in `bench/results/<date>-<model>.jsonl`; the server the bench spawns writes its games to `bench/results/bench-games.db`.

## Baseline (committed run, `bench/results/2026-10-11-stub.jsonl`)

| model | side | games | wins | draws | avg turns | avg performance_score | illegal attempts | approach | tokens | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| stub | insurgent | 3 | 0 | 3 | 30.0 | 0.2 | 0 | ink_spot x3 | 0 | 4.5 |

Reading it: the stub Red Team never wins in 30 turns and its government opponent scores 99.5–100 on the AAR, so an insurgent row shows 0–0.5. The stub makes exactly 90 tool calls per game (three per turn), attempts no illegal action, and every game replays. A host model's row is read against this floor: does it win or shorten the game, does it attempt illegal moves (the stub never does because it only submits rows from `list_legal_actions`), and what does it cost in tokens.
