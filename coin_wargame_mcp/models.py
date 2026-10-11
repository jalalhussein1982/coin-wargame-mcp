"""Pydantic output models. These ARE the tools' output schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Faction = Literal["government", "insurgent"]
SeatMode = Literal["host", "open"]
View = Literal["full", "government", "insurgent"]


# ── board ────────────────────────────────────────────────────────────────────────────


class ProvinceView(BaseModel):
    id: str
    name: str
    control: str = Field(description="government | insurgent | contested")
    sentiment: int = Field(description="-100 hostile to +100 supportive of the government")
    government_forces: int
    insurgent_forces: int
    terrain: str
    infrastructure: float
    is_capital: bool
    borders_mali: bool
    borders_algeria: bool
    borders_senegal: bool
    adjacent_provinces: list[str]
    modifiers: dict[str, float]


class FoggedProvinceView(BaseModel):
    """The government's picture: true insurgent numbers are replaced by an estimate."""

    id: str
    name: str
    control: str
    sentiment: int
    government_forces: int
    insurgent_estimated: int = Field(
        description="The government intelligence estimate apportioned to this province. Not the true count."
    )
    terrain: str
    infrastructure: float
    is_capital: bool
    borders_mali: bool
    borders_algeria: bool
    borders_senegal: bool
    adjacent_provinces: list[str]
    modifiers: dict[str, float]


class ForcesView(BaseModel):
    government_total: int
    government_reserves: int
    government_deployed: int
    insurgent_total: int | None = Field(default=None, description="Hidden in the government view.")
    insurgent_estimated_by_government: int


class GlobalStateView(BaseModel):
    turn: int
    active_faction: str
    government_resources: int
    insurgent_resources: int | None = Field(default=None, description="Hidden in the government view.")
    government_legitimacy: int
    faction_forces: ForcesView
    victory_conditions: dict[str, Any]
    scenario_conditions: dict[str, Any]


class VictoryView(BaseModel):
    winner: Faction
    condition: str
    turn: int


class GameStateView(BaseModel):
    game_id: str
    seed: int | None = Field(default=None, description="Dice seed. Null unless the caller may see it (both seats held, or game ended) and the view is full.")
    version: int = Field(description="Row version; a write that races another write fails and asks you to re-read.")
    status: Literal["active", "ended"]
    turn: int
    phase: str = Field(description="government_action | government_resolution | insurgent_action | insurgent_resolution")
    active_faction: Faction
    view: View
    global_state: GlobalStateView
    provinces: list[ProvinceView] | list[FoggedProvinceView]
    victory: VictoryView | None = None


# ── lifecycle ────────────────────────────────────────────────────────────────────────


class SeatTokens(BaseModel):
    government: str | None = None
    insurgent: str | None = None


class GameCreated(BaseModel):
    game_id: str = Field(description="Handle for every other tool. The server keeps nothing else about you.")
    seed: int | None = Field(description="The dice seed. Null when a seat is open: a seed predicts rolls for both sides.")
    seats: dict[str, SeatMode]
    seat_tokens: SeatTokens = Field(description="Only seats marked 'open' get a token; give it to the other host.")
    initial_state: GameStateView


class GameCard(BaseModel):
    game_id: str
    seed: int | None = None
    turn: int
    phase: str
    active_faction: Faction
    status: Literal["active", "ended"]
    winner: str | None = None
    created_at: str
    updated_at: str


class GameSummary(BaseModel):
    game_id: str
    seed: int
    status: Literal["ended"]
    total_turns: int
    final_state: dict[str, Any] = Field(description="Province counts, average sentiment, forces, resources, legitimacy.")
    victory: VictoryView | None = None
    actions_logged: int


class Replay(BaseModel):
    game_id: str
    seed: int | None = Field(default=None, description="Null while a seat is open and the game is active.")
    actions: int = Field(description="Turn-log entries re-run (actions, passes, IC requests).")
    reproduced: bool = Field(description="True when re-running the log from the seed rebuilt the stored state exactly.")
    divergence_at_turn: int | None = None
    final_turn: int


# ── reading ──────────────────────────────────────────────────────────────────────────


class ProvinceDetail(BaseModel):
    view: View
    province: ProvinceView | FoggedProvinceView
    text: str = Field(description="The engine's own province encoding; in the government view the insurgent line is the estimate (~).")
    legal_action_count: dict[str, int] = Field(description="Legal actions from this province per faction the caller may see.")


class Brief(BaseModel):
    faction: Faction
    turn: int
    markdown: str
    strategic_opportunities: list[str]
    recommended_doctrine: str | None = Field(default=None, description="Insurgent brief only.")


class LegalAction(BaseModel):
    action_type: str
    source_province: str
    target_province: str | None = None
    cost: int = Field(description="Base resource cost deducted on resolution.")
    min_forces: int = Field(description="Smallest forces_committed the validator accepts for this type.")
    warnings: list[str] = Field(default_factory=list)


class LegalActions(BaseModel):
    game_id: str
    faction: Faction
    turn: int
    phase: str
    your_turn: bool = Field(description="False means these actions are legal but cannot be submitted yet.")
    total: int
    truncated: bool = Field(description="True when `limit` cut the list; filter by province to see the rest.")
    actions: list[LegalAction]


class ProbabilityBreakdown(BaseModel):
    action: dict[str, Any]
    base_probability: int
    modifier_total: int
    modifier_breakdown: dict[str, int]
    modifier_explanations: list[str]
    final_probability: int = Field(description="Percent chance of success or better, clamped 5..95.")
    outcome_probabilities: dict[str, float] = Field(description="Percent mass per outcome band.")
    validation: dict[str, Any] = Field(description="is_valid, errors, warnings for this exact action.")


class DoctrineAdvice(BaseModel):
    doctrine: Literal["AQ_NETWORK", "ISIS_TERRITORIAL", "TALIBAN_ADAPTIVE"]
    rationale: str
    description_uri: str = Field(description="Read this resource for the full doctrine text.")
    preferred_actions: list[str]


# ── acting ───────────────────────────────────────────────────────────────────────────


class Action(BaseModel):
    """Input model for submit_action and preview_action."""

    action_type: str = Field(description="One of the engine's action types, e.g. 'patrol', 'ambush'. See wargame://rules/actions.")
    source_province: str
    target_province: str | None = None
    forces_committed: int = Field(default=0, ge=0)
    resources_spent: int = Field(default=0, ge=0, description="Extra resources beyond the base cost. Usually 0.")


class NextTurn(BaseModel):
    turn: int
    active_faction: Faction
    phase: str


class TurnResult(BaseModel):
    game_id: str
    version: int
    turn_played: int
    faction: Faction
    adjudication: dict[str, Any] | None = Field(description="Outcome, rolls, narrative, mutations. None for pass_turn.")
    mutations: list[dict[str, Any]]
    victory: VictoryView | None = None
    status: Literal["active", "ended"]
    next: NextTurn
    new_state: GameStateView


class ICResponseView(BaseModel):
    game_id: str
    version: int
    request_type: str
    target: str | None
    decision: Literal["approved", "conditional", "delayed", "denied"]
    conditions: list[str]
    magnitude: float
    timeline: str
    backlash_effects: list[str]
    narrative: str


# ── learning ─────────────────────────────────────────────────────────────────────────


class PostTurnFeedbackView(BaseModel):
    game_id: str
    turn: int
    action: dict[str, Any]
    outcome: str
    action_analysis: str
    red_team_prediction: str
    missed_opportunities: list[str]
    doctrinal_alignment: str
    sentiment_forecast: str
    recommendations: list[str]
    historical_parallel: str
    outcome_explanation: str
    key_insight: str


class AARView(BaseModel):
    game_id: str
    outcome: Literal["victory", "defeat", "stalemate"] = Field(description="From the government's seat.")
    performance_score: float
    executive_summary: str
    strategic_assessment: str
    turning_points: list[dict[str, Any]]
    pattern_analysis: str
    doctrine_compliance: dict[str, Any]
    lessons_learned: list[str]
    improvement_areas: list[str]
    campaign_narrative: str
    red_team_analysis: str
    historical_comparisons: list[str]


class StrategyProfileView(BaseModel):
    game_id: str
    approach: str
    secondary_approach: str | None
    confidence: float
    approach_scores: dict[str, float]
    evolution: str
    doctrinal_comparison: str
    effectiveness_assessment: str
    recommendations: list[str]
    evidence: list[str]
    historical_parallels: list[str]
    government_actions_seen: int


class PatternView(BaseModel):
    game_id: str
    favorite_actions: list[str]
    geographic_focus: list[str]
    temporal_patterns: dict[str, str]
    resource_behavior: str
    risk_profile: str
    tendencies: list[dict[str, Any]]
    predictability_score: float
    vulnerability_assessment: str
    improvement_over_time: str
    response_to_adversity: str
    force_commitment_pattern: str
