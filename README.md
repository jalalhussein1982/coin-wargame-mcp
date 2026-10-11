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
| **Structured output over a real domain.** 13 provinces, 36 action types, and fog of war enforced on every government-facing payload: the true insurgent count is a field that does not exist in the government's view, the estimate is a server-side lagged noisy model, previews run against it, and the seed is withheld while another host holds a seat. | `models.py`, `tests/test_fog.py`, `tests/test_views.py` |
| **Prompts as doctrine.** The Red Team's system prompt and the three insurgent doctrines become prompts and resources a host model plays from. | `play_red_team`, `wargame://doctrine/*` |
| **A benchmark that is an MCP client.** It never imports the engine; it measures a model's tool use (wins, illegal attempts, tokens) against a rule-based floor. | `bench/`, `docs/benchmark.md` |

## Run

```bash
git clone --recurse-submodules <this repo> && cd coin-wargame-mcp
make install                      # git submodule update --init; uv sync --all-extras
make test                         # uv run pytest -q  -> 73 passed
make stdio                        # uv run python -m coin_wargame_mcp.server
make inspector                    # uv run mcp dev coin_wargame_mcp/server.py
make http PORT=8000               # --http --no-auth: loopback only, Host/Origin checked
make bench GAMES=3                # stub vs stub over stdio, no API key
```

Games are stored in `COIN_WARGAME_DB` (default `games.db` in the working directory). The server never calls an LLM: `GEMINI_API_KEY` is removed from its environment when the engine shim is imported and the engine's rule-based paths run. The project runs from a checkout (`uv sync` installs it editable): the shim locates the engine at `vendor/mauritania-coin-wargame` relative to the repository and raises a clear error naming `git submodule update --init` if it is missing; a wheel installed elsewhere would not carry the engine.

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

Streamable HTTP with bearer auth (for two hosts sharing a game, or a remote bench). Clients reach the server by a public name, so give that name with `--public-url`; the resource URL, the `aud` tokens must carry, and the Host/Origin allow-lists all derive from it:

```bash
OAUTH_ISSUER_URL=https://auth.example.com \
OAUTH_SHARED_SECRET='a-shared-secret-of-at-least-32-bytes.....' \
  uv run python -m coin_wargame_mcp.server --http --host 0.0.0.0 --port 8000 \
      --public-url https://wargame.example.com/mcp
# demo tokens instead of (or as well as) JWTs:
OAUTH_STATIC_TOKENS='{"demo-token": {"client_id": "demo", "scopes": ["wargame:play"]}}'
# extra Host values a proxy may send: ALLOWED_HOSTS=internal.example:*,10.0.0.5:*
```

Tokens: static tokens from `OAUTH_STATIC_TOKENS`, or HS256 JWTs signed with `OAUTH_SHARED_SECRET` (32+ bytes) with `iss` = `OAUTH_ISSUER_URL`, `aud` = the public URL, a numeric `exp`, and `scope` containing `wargame:play`. Requests without a valid token get 401 and a `resource_metadata` pointer. The server refuses to start when auth is configured without a secret or static tokens, or when `--host` is not loopback and no `--public-url`/`OAUTH_RESOURCE_URL` is given. `--no-auth` binds `127.0.0.1` only (a foreign `Host` gets 421, a foreign `Origin` 403).

## Tools

Registered in this order. Reads carry `read_only_hint=True`; writes `destructive_hint=False`; all `open_world_hint=False`.

| Tool | Kind | What it does |
|---|---|---|
| `new_game(seed?, scenario?, seats?)` | write | Mint a handle; seats `host` or `open` (open seats get a `seat_token`). Returns the board as the creator may see it; the seed is withheld when a seat is open. |
| `list_games(status?, limit, seat_token?)` | read | Game cards, most recently touched first. Open-seat games appear only to a token holder; seeds only when visible. |
| `end_game(game_id, seat_token?)` | write, idempotent | End now (a token of an open seat is required while one is open); returns the engine's session summary; unlocks `after_action_review`. |
| `replay(game_id)` | read | Re-run the log from the seed, checking a recorded state hash after every entry; `reproduced`, `divergence_at_turn`. Runs under the currently vendored rules. |
| `get_state(game_id, view?, seat_token?)` | read | The board as the caller may see it. `full`/`insurgent` need the insurgent seat while it is open; `government` has `insurgent_estimated` and no `insurgent_forces`. |
| `get_province(game_id, province_id, view?, seat_token?)` | read | One province, the engine's text encoding, legal-action counts for the factions the caller may see. |
| `situation_brief(game_id, faction, seat_token?)` | read | Markdown assessment; the insurgent one is the engine's own Red Team encoding and needs that seat while open. |
| `list_legal_actions(game_id, faction, province?, limit, seat_token?)` | read | Every action the validator accepts now, at minimum legal commitment. Rows are submittable as-is. Insurgent lists need that seat while open. |
| `preview_action(game_id, faction, action, seat_token?)` | read | Base rate, each modifier with its explanation, final percent, outcome bands, validation. No roll. Government previews use the estimate, not the true count. |
| `recommend_doctrine(game_id, seat_token?)` | read | The Red Team's own doctrine heuristic, as advice (reads true strength; insurgent seat while open). |
| `submit_action(game_id, faction, action, seat_token?, rationale?)` | write | Validate, adjudicate (seeded d100), mutate, cascade, advance, check victory, persist. Illegal: error with reasons and alternatives. Returns the acting seat's view. An insurgent win on turn N is stamped N+1 (victory is checked after the phase advances, as in the engine). |
| `request_international_community(game_id, request_type, justification, target?, seat_token?)` | write | Government only, in its action phase; the IC oracle's rule-based path, seeded from the game dice. Narrative only: the engine applies no mutation for it. |
| `pass_turn(game_id, faction, seat_token?)` | write | Advance without acting. |
| `turn_feedback(game_id, turn?)` | read | The engine's post-turn analysis of a government move. |
| `after_action_review(game_id)` | read | AAR with `performance_score`; ended games only. |
| `strategy_profile(game_id)` | read | COIN approach classification with evidence. |
| `pattern_report(game_id)` | read | Favourites, geography, tempo, risk, predictability. |

Resources: `wargame://doctrine/{aq_network|isis_territorial|taliban_adaptive}`, `wargame://rules/actions`, `wargame://rules/probability-tables`, `wargame://rules/map` (1 h, public); `wargame://game/{id}` (fogged view while the insurgent seat is open) and `wargame://game/{id}/log` (withheld while any seat is open; readable once the game ends), both 0 ms, private. Prompts: `play_red_team(game_id, doctrine?)`, `play_government(game_id, approach?)`, `after_action_debrief(game_id)`.

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
* **Fog by type, not by null, and enforced by the server.** The government view returns a `FoggedProvinceView` with no `insurgent_forces` field; the SDK would otherwise emit `null` keys. Every tool returns the view the caller is entitled to (acting seat for writes, the most it may see for reads); while another host holds the insurgent seat, full views, insurgent briefs, insurgent legal actions and previews, doctrine advice, the log resource, `end_game` and the seed all need that seat's token.
* **The estimate is the server's.** The engine's `insurgent_estimated_by_government` is a constant. The server keeps a per-province estimate (seeded noise in [0.75, 1.25] on each report, blended 70/30 into the previous value after every insurgent phase, derived from `(seed, turn, province)` so it does not disturb the dice), and runs government previews against a copy of the state with those numbers, so the engine's own force-ratio text never carries the true count. The roll uses the truth.

## Tests

`uv run pytest -q` → **73 passed in about 8 s**: protocol surface (order, annotations, schemas, cache hints), lifecycle, fog of war on every government-facing payload with an open insurgent seat, the estimate model, a hypothesis property that every row of `list_legal_actions` validates over seeds 0–40 with random play and that the filter rejected candidates, adjudication and illegal-action handling, phase and seat guards, two-writer concurrency including a real two-client race, a ten-turn seed-7 replay (with an IC request) and tamper detection on state, dice state, controller and log, fog views and briefs, learning tools, victory, resources and prompts, stub benchmark over MCP, HTTP with Host/Origin checks, bearer auth (malformed tokens, `exp`, `aud`, secret length, settings builder), the Anthropic provider's stop reasons against a fake client, and ten eval QA pairs (`evals/coin-wargame.xml`) asserted against a live replay.

## Layout

```
coin_wargame_mcp/  server.py engine.py games.py seats.py brief.py learning.py auth.py models.py _engine.py
bench/             __main__.py providers.py scoring.py results/
tests/             18 test modules + scripted.py (the seed-7 script)
evals/             coin-wargame.xml
docs/              architecture.md use-cases.md benchmark.md superpowers/{specs,plans}
vendor/            mauritania-coin-wargame (submodule)
```

## Related servers in this portfolio

- [`ihkam-eval-mcp`](https://github.com/jalalhussein1982/ihkam-eval-mcp) — read-only evaluation harness over a legal RAG (stdio)
- `phase1/siem-mcp` in [`conflict-trends-dashboard`](https://github.com/jalalhussein1982/conflict-trends-dashboard) — conflict analytics over PostGIS (stdio)
- `energy_platform/mcp` in [`energy-platform`](https://github.com/jalalhussein1982/energy-platform) — the contributor path of an ingestion platform (stdio)
- `hostel-ops-mcp`, `geoecon-pipeline-mcp`, `coin-wargame-mcp` — this series: remote transport and OAuth, long-running jobs, and MCP as an agent harness
