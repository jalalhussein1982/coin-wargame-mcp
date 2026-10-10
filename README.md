# coin-wargame-mcp

MCP server over the Mauritania COIN wargame engine: any host model plays Government or Red Team through tools; seeded replayable games; a model benchmark harness.

Built on [`jalalhussein1982/mauritania-coin-wargame`](https://github.com/jalalhussein1982/mauritania-coin-wargame). MCP protocol revision 2026-07-28, MCP Python SDK 2.x.

**Status:** design stage. The design document is at [`docs/superpowers/specs/2026-10-11-coin-wargame-mcp-design.md`](docs/superpowers/specs/2026-10-11-coin-wargame-mcp-design.md). Implementation follows once the design is approved.

## Why this exists

The wargame's insurgent Red Team was an LLM agent wired to one vendor through bespoke glue. This server replaces the glue with MCP so any host model can play either seat through tools, games persist as server-minted handles on a stateless transport with seeded, replayable dice, and a benchmark harness that only speaks MCP compares models on identical scenarios.

## Related servers in this portfolio

- [`ihkam-eval-mcp`](https://github.com/jalalhussein1982/ihkam-eval-mcp) — read-only evaluation harness over a legal RAG (stdio)
- `phase1/siem-mcp` in [`conflict-trends-dashboard`](https://github.com/jalalhussein1982/conflict-trends-dashboard) — conflict analytics over PostGIS (stdio)
- `energy_platform/mcp` in [`energy-platform`](https://github.com/jalalhussein1982/energy-platform) — the contributor path of an ingestion platform (stdio)
- `hostel-ops-mcp`, `geoecon-pipeline-mcp`, `coin-wargame-mcp` — this series: remote transport and OAuth, long-running jobs, and MCP as an agent harness
