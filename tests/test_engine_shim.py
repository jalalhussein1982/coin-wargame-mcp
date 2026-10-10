"""The shim exposes the vendored engine as `src.*` and strips the LLM key."""

from __future__ import annotations

import os


def test_engine_imports_and_loads_initial_state(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "should-be-removed")
    import importlib

    from coin_wargame_mcp import _engine as E

    importlib.reload(E)
    gs = E.GameStateStore()
    gs.load_initial_state(E.INITIAL_STATE_PATH)
    assert len(gs.get_all_provinces()) == 13
    assert gs.get_province("nouakchott").is_capital is True
    assert "GEMINI_API_KEY" not in os.environ


def test_engine_components_run_rule_based_without_google():
    import sys

    from coin_wargame_mcp import _engine as E

    # None of the imported modules may have pulled google's SDK in at import time.
    assert not any(m.startswith("google") for m in sys.modules)
    assert E.ICOracle()._model is None
    assert E.PostTurnAnalyzer().api_key is None
    assert E.AARGenerator().api_key is None
    assert E.StrategyClassifier().api_key is None


def test_doctrine_and_prompt_files_exist():
    from coin_wargame_mcp import _engine as E

    assert sorted(p.stem for p in E.DOCTRINE_DIR.glob("*.md")) == [
        "aq_network", "isis_territorial", "taliban_adaptive",
    ]
    assert (E.PROMPTS_DIR / "red_team_system.md").is_file()
    assert E.rules_engine.ACTION_COSTS[E.ActionType.PATROL] == 3
