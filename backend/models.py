from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field


ActionType = Literal[
    "click", "type", "scroll", "press_key", "back", "wait", "finish", "fail", "dismiss_modal"
]


class RunCreate(BaseModel):
    goal: str = Field(min_length=3, max_length=1000)
    target_url: str = Field(default="http://127.0.0.1:8000/demo/")
    max_steps: int = Field(default=18, ge=4, le=40)
    exploration_passes: int = Field(default=1, ge=1, le=3)


class AgentAction(BaseModel):
    action: ActionType
    element_id: str | None = None
    text: str | None = None
    key: str | None = None
    scroll_y: int | None = None
    rationale: str = ""
    expected_result: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    goal_complete: bool = False
    stuck: bool = False


class Issue(BaseModel):
    id: str
    category: Literal["accessibility", "ux", "navigation", "visual", "regression"]
    severity: Literal["low", "medium", "high", "critical"]
    title: str
    description: str
    evidence: str = ""
    recommendation: str = ""
    step: int | None = None
    screenshot: str | None = None
    source: Literal["heuristic", "agent", "both"] = "heuristic"


class Event(BaseModel):
    seq: int = 0
    step: int = 0
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class JourneyNode(BaseModel):
    id: str
    label: str
    url: str
    step: int
    screenshot: str | None = None
    is_goal: bool = False
    is_loop: bool = False


class JourneyEdge(BaseModel):
    source: str
    target: str
    action: str
    label: str
    pass_no: int = 1


class JourneyGraph(BaseModel):
    nodes: list[JourneyNode] = Field(default_factory=list)
    edges: list[JourneyEdge] = Field(default_factory=list)


class SubgoalState(BaseModel):
    id: str
    description: str
    status: Literal["pending", "in_progress", "verified", "failed"] = "pending"
    evidence: str = ""
    required: bool = True
    observable_signals: list[str] = Field(default_factory=list)


class GoalPlan(BaseModel):
    primary_goal: str
    subgoals: list[SubgoalState] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)


class ModelDecision(BaseModel):
    provider: str
    model: str
    role: str = "action_planner"
    action: ActionType
    element_id: str | None = None
    text: str | None = None
    key: str | None = None
    scroll_y: int | None = None
    confidence: float = 0.5
    rationale: str = ""
    expected_result: str = ""
    goal_complete: bool = False
    stuck: bool = False
    latency_ms: int = 0


class ConsensusResult(BaseModel):
    agreed: bool = True
    selected_action: ActionType = "wait"
    selected_element_id: str | None = None
    selected_text: str | None = None
    selected_key: str | None = None
    confidence: float = 0.5
    rationale: str = ""
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    critic_verdict: str = ""
    disagreement_reason: str | None = None


class ActionResult(BaseModel):
    ok: bool = True
    state_changed: bool = False
    url_changed: bool = False
    content_changed: bool = False
    verification: str = "passed"
    before_url: str = ""
    after_url: str = ""
    error: str | None = None


class NavigationDiagnostics(BaseModel):
    """Structured result of a browser navigation attempt."""
    navigation_state: Literal["usable", "loading", "blank", "blocked", "error", "unknown"] = "unknown"
    attempts: int = 0
    final_url: str = ""
    title: str = ""
    body_text_length: int = 0
    interactive_elements: int = 0
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)
    request_failures: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


class RunState(BaseModel):
    id: str
    goal: str
    target_url: str
    # URL diagnostics — populated after normalization and navigation
    original_target_url: str = ""
    normalized_target_url: str = ""
    current_url: str = ""
    last_observed_url: str = ""
    final_url: str = ""
    redirect_chain: list[dict[str, Any]] = Field(default_factory=list)
    navigation_state: str = "unknown"
    navigation_diagnostics: NavigationDiagnostics = Field(default_factory=NavigationDiagnostics)
    # ── Phase: single source of truth for the frontend state machine ──
    # queued | starting | navigating | page_ready | exploring | verifying
    # | auditing | completed | partial | failed | cancelled | blocked
    phase: str = "queued"
    state_version: int = 1
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Browser and page explicit states
    browser_state: str = "starting"  # starting | started | navigating | ready | failed | closed
    content_state: str = "loading"   # loading | ready | blank | blocked | error
    page_status: str = "loading"     # loading | ready | blank | blocked | error

    # Screenshot state: pending | available | failed
    screenshot_status: str = "pending"
    screenshot_step: int = 0
    screenshot_timestamp: str | None = None

    # Step and event tracking
    latest_step: int = 0
    latest_event: str = "queued"

    # Goal and verification explicit states
    goal_status: str = "pending"  # pending | verified | partial | failed
    goal_verified: bool = False

    # Audit and report status
    audit_status: str = "pending"   # pending | running | completed
    report_status: str = "pending"  # pending | generating | ready

    # Run lifecycle
    status: Literal["queued", "running", "completed", "failed", "cancelled", "partial", "blocked"] = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    step_count: int = 0
    paths_discovered: int = 0
    goal_completed: bool = False
    goal_verification_evidence: str = ""
    subgoals: list[dict[str, Any]] = Field(default_factory=list)
    consensus_history: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    latest_screenshot: str | None = None
    report_url: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    journey: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})
    trajectory: list[dict[str, Any]] = Field(default_factory=list)



