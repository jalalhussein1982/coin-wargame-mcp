"""Import shim for the vendored wargame engine.

The engine (``vendor/mauritania-coin-wargame``) has no ``pyproject.toml`` and its
modules import each other as ``src.*``. This module puts the submodule root on
``sys.path`` once, removes ``GEMINI_API_KEY`` from the process environment so every
engine component takes its rule-based path (the server makes no LLM calls), and
re-exports the handful of engine objects the server uses.

Nothing under ``vendor/`` is modified. Engine quirks are handled in this package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor" / "mauritania-coin-wargame"

if not (VENDOR_ROOT / "src").is_dir():  # pragma: no cover - environment problem, not logic
    raise ImportError(
        f"Engine submodule missing at {VENDOR_ROOT}. Run: git submodule update --init"
    )

if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

# The server never talks to an LLM. The engine's ICOracle, PostTurnAnalyzer,
# AARGenerator and StrategyClassifier read this variable at construction time and
# switch to their rule-based paths when it is absent.
os.environ.pop("GEMINI_API_KEY", None)

# ruff: noqa: E402  (imports below depend on the sys.path edit above)
from src.agents.red_team import doctrine_selector, state_encoder  # type: ignore[import-not-found]
from src.agents.red_team.doctrine_selector import DoctrineName  # type: ignore[import-not-found]
from src.arbiter.cascade_engine import CascadeEngine  # type: ignore[import-not-found]
from src.arbiter.ic_oracle import ICOracle, ICRequest, ICResponse  # type: ignore[import-not-found]
from src.arbiter.outcome_resolver import OutcomeResolver, OutcomeResult  # type: ignore[import-not-found]
from src.arbiter.state_mutator import StateMutator  # type: ignore[import-not-found]
from src.core import rules_engine  # type: ignore[import-not-found]
from src.core.game_state import GameStateStore  # type: ignore[import-not-found]
from src.core.session_controller import (  # type: ignore[import-not-found]
    SessionController,
    SessionState,
    TurnPhase,
    VictoryResult,
)
from src.learning.after_action_review import AARGenerator, AfterActionReview  # type: ignore[import-not-found]
from src.learning.pattern_detector import PatternAnalysis, PatternDetector  # type: ignore[import-not-found]
from src.learning.post_turn_analysis import PostTurnAnalyzer, PostTurnFeedback  # type: ignore[import-not-found]
from src.learning.strategy_classifier import (  # type: ignore[import-not-found]
    COINApproach,
    StrategyClassifier,
    StrategyProfile,
)
from src.schemas import (  # type: ignore[import-not-found]
    ALL_PROVINCE_IDS,
    GOVERNMENT_ACTION_TYPES,
    INSURGENT_ACTION_TYPES,
    Action,
    ActionType,
    ActionValidation,
    AdjudicationResult,
    ControlStatus,
    GovernmentAction,
    InsurgentAction,
    Mutation,
    OutcomeType,
    ProvinceState,
    Terrain,
)
from src.utils.dice import DiceRoller  # type: ignore[import-not-found]

INITIAL_STATE_PATH = str(VENDOR_ROOT / "src" / "config" / "mauritania_initial_state.json")
PROBABILITY_TABLES_PATH = str(VENDOR_ROOT / "src" / "config" / "probability_tables.json")
DOCTRINE_DIR = VENDOR_ROOT / "doctrine"
PROMPTS_DIR = VENDOR_ROOT / "prompts"

__all__ = [
    "AARGenerator", "ALL_PROVINCE_IDS", "Action", "ActionType", "ActionValidation",
    "AdjudicationResult", "AfterActionReview", "COINApproach", "CascadeEngine",
    "ControlStatus", "DOCTRINE_DIR", "DiceRoller", "DoctrineName", "GOVERNMENT_ACTION_TYPES",
    "GameStateStore", "GovernmentAction", "ICOracle", "ICRequest", "ICResponse",
    "INITIAL_STATE_PATH", "INSURGENT_ACTION_TYPES", "InsurgentAction", "Mutation",
    "OutcomeResolver", "OutcomeResult", "OutcomeType", "PROBABILITY_TABLES_PATH", "PROMPTS_DIR",
    "PatternAnalysis", "PatternDetector", "PostTurnAnalyzer", "PostTurnFeedback", "ProvinceState",
    "SessionController", "SessionState", "StateMutator", "StrategyClassifier", "StrategyProfile",
    "Terrain", "TurnPhase", "VENDOR_ROOT", "VictoryResult", "doctrine_selector", "rules_engine",
    "state_encoder",
]
