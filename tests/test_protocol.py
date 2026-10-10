"""Protocol surface: tool order, annotations, output schemas, resources, prompts, cache hints."""

from __future__ import annotations

import pytest

from mcp import Client

TOOL_ORDER = [
    "new_game", "list_games", "end_game", "replay",
    "get_state", "get_province", "situation_brief", "list_legal_actions", "preview_action", "recommend_doctrine",
    "submit_action", "request_international_community", "pass_turn",
    "turn_feedback", "after_action_review", "strategy_profile", "pattern_report",
]
READ_ONLY = {"list_games", "replay", "get_state", "get_province", "situation_brief", "list_legal_actions",
             "preview_action", "recommend_doctrine", "turn_feedback", "after_action_review", "strategy_profile",
             "pattern_report"}


@pytest.fixture
async def client():
    from coin_wargame_mcp.server import mcp

    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_tools_are_listed_in_spec_order_with_output_schemas(client: Client):
    tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == TOOL_ORDER
    for t in tools:
        assert t.output_schema is not None, t.name
        assert t.title, t.name
        assert t.description and len(t.description) > 20, t.name


@pytest.mark.anyio
async def test_annotations_match_the_spec(client: Client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    for name, t in tools.items():
        a = t.annotations
        assert a is not None, name
        assert a.open_world_hint is False, name
        assert a.read_only_hint is (name in READ_ONLY), name
        if name not in READ_ONLY:
            assert a.destructive_hint is False, name
    assert tools["new_game"].annotations.idempotent_hint is False
    assert tools["end_game"].annotations.idempotent_hint is True


@pytest.mark.anyio
async def test_lists_carry_public_cache_hints(client: Client):
    res = await client.list_tools()
    assert res.ttl_ms == 3_600_000 and res.cache_scope == "public"
    res = await client.list_prompts()
    assert res.ttl_ms == 3_600_000 and res.cache_scope == "public"


@pytest.mark.anyio
async def test_resources_and_templates_are_declared(client: Client):
    uris = {str(r.uri) for r in (await client.list_resources()).resources}
    assert {"wargame://rules/actions", "wargame://rules/probability-tables", "wargame://rules/map"} <= uris
    templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}
    assert {"wargame://doctrine/{name}", "wargame://game/{game_id}", "wargame://game/{game_id}/log"} <= templates


@pytest.mark.anyio
async def test_prompts_are_declared(client: Client):
    names = [p.name for p in (await client.list_prompts()).prompts]
    assert names == ["play_red_team", "play_government", "after_action_debrief"]


@pytest.mark.anyio
async def test_server_instructions_point_at_new_game(client: Client):
    assert "new_game" in (client.instructions or "")
