# coin-wargame-mcp

An MCP server over the [Mauritania COIN wargame](https://github.com/jalalhussein1982/mauritania-coin-wargame) engine. Any host model plays Government or Red Team through tools; games are server-minted handles with seeded, replayable dice; a benchmark harness that only speaks MCP compares models on identical scenarios.

MCP protocol revision 2026-07-28, MCP Python SDK 2.x (`mcp>=2.2`), Python 3.12, `uv`. The engine is a git submodule under `vendor/` and is never modified.

## What it proves

The wargame already had an LLM agent: the insurgent Red Team, wired to one vendor through a Markdown state encoder, a JSON action decoder and retry glue. This server replaces that glue with MCP.

| Claim | Where it is shown |
|---|---|
| **MCP as an agent harness, not a data connector.** Write tools advance a deterministic engine; validation failures come back as errors the model can act on (the engine's reasons plus legal alternatives). | `submit_action`, `docs/use-cases.md` §1 |
| **Stateful games on a stateless transport.** Every tool takes `game_id`; the server keeps nothing in memory; each call loads a SQLite row, rebuilds the engine, acts and writes back with a version check. Two hosts can share one game. | `games.py`, `tests/test_concurrency.py`, use case §3 |
| **Reproducibility as a tool.** Dice state is persisted after every call; `replay(game_id)` re-runs the log from the seed and reports whether it rebuilt the stored state. Two unseeded engine paths were found and wrapped. | `engine.py`, `tests/test_replay.py`, use case §4 |
| **Structured output over a real domain.** 13 provinces, 36 action types, a fog-of-war view that removes a field rather than nulling it. | `models.py`, `tests/test_views.py` |
| **Prompts as doctrine.** The Red Team's system prompt and the three insurgent doctrines become prompts and resources a host model plays from. | `play_red_team`, `wargame://doctrine/*` |
| **A benchmark that is an MCP client.** It never imports the engine; it measures a model's tool use (wins, illegal attempts, tokens) against a rule-based floor. | `bench/`, `docs/benchmark.md` |

## Run

```bash
git clone --recurse-submodules <this repo> && cd coin-wargame-mcp
make install                      # git submodule update --init; uv sync --all-extras
make test                         # uv run pytest -q  -> 58 passed
make stdio                        # uv run python -m coin_wargame_mcp.server
make inspector                    # uv run mcp dev coin_wargame_mcp/server.py
make http PORT=8000               # --http --no-auth: loopback only, Host/Origin checked
make bench GAMES=3                # stub vs stub over stdio, no API key
```

Games are stored in `COIN_WARGAME_DB` (default `games.db` in the working directory). The server never calls an LLM: `GEMINI_API_KEY` is removed from its environment and the engine's rule-based paths run.

### Host configuration

Claude Code (`.mcp.json`, included):

```json
{"mcpServers": {"coin-wargame": {"command": "uv",
  "args": ["run", "--directory", ".", "python", "-m", "coin_wargame_mcp.server"],
  "env": {"COIN_WARGAME_DB": "games.db"}}}}
```

Claude Desktop (`claude_desktop_config.json`), with an absolute path:

```json
{"mcpServers": {"coin-wargame": {"command": "uv",
  "args": ["run", "--directory", "/path/to/coin-wargame-mcp", "python", "-m", "coin_wargame_mcp.server"]}}}
```

Streamable HTTP with bearer auth (for two hosts sharing a game, or a remote bench):

```bash
OAUTH_ISSUER_URL=https://auth.example.com OAUTH_SHARED_SECRET=... \
  uv run python -m coin_wargame_mcp.server --http --host 0.0.0.0 --port 8000
# or OAUTH_STATIC_TOKENS='{"demo-token": {"client_id": "demo", "scopes": ["wargame:play"]}}' for a demo
```

Clients connect to `http://host:8000/mcp` with `Authorization: Bearer <token>`; requests without one get 401 and a `resource_metadata` pointer. `--no-auth` binds `127.0.0.1` only.

## Tools

Registered in this order. Reads carry `read_only_hint=True`; writes `destructive_hint=False`; all `open_world_hint=False`.

| Tool | Kind | What it does |
|---|---|---|
| `new_game(seed?, scenario?, seats?)` | write | Mint a handle; seats `host` or `open` (open seats get a `seat_token`). Same seed, same actions, same game. |
| `list_games(status?, limit)` | read | Game cards, most recently touched first. |
| `end_game(game_id)` | write, idempotent | End now; returns the engine's session summary; unlocks `after_action_review`. |
| `replay(game_id)` | read | Re-run the log from the seed; `reproduced`, `divergence_at_turn`. |
| `get_state(game_id, view)` | read | Full board. `view=government` applies the fog rule (`insurgent_estimated`, no `insurgent_forces`). |
| `get_province(game_id, province_id)` | read | One province, the engine's text encoding, legal-action counts. |
| `situation_brief(game_id, faction)` | read | Markdown assessment; the insurgent one is the engine's own Red Team encoding. |
| `list_legal_actions(game_id, faction, province?, limit)` | read | Every action the validator accepts now, at minimum legal commitment. Rows are submittable as-is. |
| `preview_action(game_id, faction, action)` | read | Base rate, each modifier with its explanation, final percent, outcome bands, validation. No roll. |
| `recommend_doctrine(game_id)` | read | The Red Team's own doctrine heuristic, as advice. |
| `submit_action(game_id, faction, action, seat_token?, rationale?)` | write | Validate, adjudicate (seeded d100), mutate, cascade, advance, check victory, persist. Illegal: error with reasons and alternatives. |
| `request_international_community(game_id, request_type, justification, target?, seat_token?)` | write | Government only; the IC oracle's rule-based path, seeded from the game dice. |
| `pass_turn(game_id, faction, seat_token?)` | write | Advance without acting. |
| `turn_feedback(game_id, turn?)` | read | The engine's post-turn analysis of a government move. |
| `after_action_review(game_id)` | read | AAR with `performance_score`; ended games only. |
| `strategy_profile(game_id)` | read | COIN approach classification with evidence. |
| `pattern_report(game_id)` | read | Favourites, geography, tempo, risk, predictability. |

Resources: `wargame://doctrine/{aq_network|isis_territorial|taliban_adaptive}`, `wargame://rules/actions`, `wargame://rules/probability-tables`, `wargame://rules/map` (1 h, public); `wargame://game/{id}`, `wargame://game/{id}/log` (0 ms, private). Prompts: `play_red_team(game_id, doctrine?)`, `play_government(game_id, approach?)`, `after_action_debrief(game_id)`.

Errors a model can fix are `ToolError`s: unknown game (names `list_games`), wrong seat or phase (names whose turn it is), illegal action (errors plus alternatives), stale version (`call get_state`), finished game. Anything else is a crash and stays one.

## Benchmark

`make bench GAMES=3` (stub vs stub, committed as `bench/results/2026-10-11-stub.jsonl`):

| model | side | games | wins | draws | avg turns | avg performance_score | illegal attempts | approach | tokens | seconds |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| stub | insurgent | 3 | 0 | 3 | 30.0 | 0.2 | 0 | ink_spot x3 | 0 | 4.5 |

The stub is the floor: it submits only rows from `list_legal_actions` (no illegal attempts), makes three calls a turn, never wins, and every game replays. To put a model on the table:

```bash
ANTHROPIC_API_KEY=... uv run python -m bench --games 5 --red anthropic --model claude-sonnet-5-5
```

The Anthropic provider is a Messages API tool-use loop over the server's tool list; it imports without a key and fails only when called. `docs/benchmark.md` defines every column.

## Design decisions

* **Stateless handles.** No sessions, no per-game memory. The row carries `version`; `save` is `UPDATE ... WHERE version = ?`, so a racing writer is told to re-read instead of overwriting.
* **Seeded dice, persisted.** `random.Random.getstate()` is stored after every call. The controller's `datetime.now()` in `turn_history` and the IC oracle's module-level `random.random()` were the two unseeded paths; the first is avoided by mirroring the four-phase machine without a timestamp, the second by seeding the global generator from the game dice for one call and restoring it.
* **No LLM in the server.** The engine's `ICOracle`, `PostTurnAnalyzer`, `AARGenerator` and `StrategyClassifier` have rule-based paths that run when the key is absent; the shim removes it.
* **Engine quirks handled here, not upstream.** The engine's controller nests `faction` inside `action`, which its own strategy classifier never reads, so it saw zero government actions; the server's log carries `faction` at the top level. `territorial_seizure` never relocates forces upstream; control changes come from casualties shifting the ratio. Both are documented in `docs/superpowers/plans/2026-10-11-coin-wargame-mcp-notes.md`.
* **Fog by type, not by null.** The government view returns a `FoggedProvinceView` with no `insurgent_forces` field; the SDK would otherwise emit `null` keys.

## Tests

`uv run pytest -q` → **58 passed in about 6 s**: protocol surface (order, annotations, schemas, cache hints), lifecycle, a hypothesis property that every row of `list_legal_actions` validates over seeds 0–20 with random play, adjudication and illegal-action handling, phase and seat guards, two-writer concurrency, a ten-turn seed-7 replay (with an IC request) and a tampered-row detection, fog views and briefs, learning tools, victory, resources and prompts, stub benchmark over MCP, HTTP with Host/Origin checks, bearer auth, and ten eval QA pairs (`evals/coin-wargame.xml`) asserted against a live replay.

## Layout

```
coin_wargame_mcp/  server.py engine.py games.py seats.py brief.py learning.py auth.py models.py _engine.py
bench/             __main__.py providers.py scoring.py results/
tests/             14 test modules + scripted.py (the seed-7 script)
evals/             coin-wargame.xml
docs/              architecture.md use-cases.md benchmark.md superpowers/{specs,plans}
vendor/            mauritania-coin-wargame (submodule)
```

## Related servers in this portfolio

- [`ihkam-eval-mcp`](https://github.com/jalalhussein1982/ihkam-eval-mcp) — read-only evaluation harness over a legal RAG (stdio)
- `phase1/siem-mcp` in [`conflict-trends-dashboard`](https://github.com/jalalhussein1982/conflict-trends-dashboard) — conflict analytics over PostGIS (stdio)
- `energy_platform/mcp` in [`energy-platform`](https://github.com/jalalhussein1982/energy-platform) — the contributor path of an ingestion platform (stdio)
- `hostel-ops-mcp`, `geoecon-pipeline-mcp`, `coin-wargame-mcp` — this series: remote transport and OAuth, long-running jobs, and MCP as an agent harness
