from __future__ import annotations

from typing import Any
from .models import Issue


def build_heuristic_findings(
    trajectory: list[dict[str, Any]],
    accessibility: list[dict[str, Any]],
    layout: list[dict[str, Any]],
) -> list[Issue]:
    findings: list[Issue] = []

    # 1. Repeated interaction heuristic (friction)
    repeated_clicks: dict[str, int] = {}
    for item in trajectory:
        eid = item.get("element_id")
        action = item.get("action")
        if eid and action == "click":
            repeated_clicks[eid] = repeated_clicks.get(eid, 0) + 1

    for eid, count in repeated_clicks.items():
        if count >= 3:
            findings.append(
                Issue(
                    id=f"repeat-click-{eid}",
                    category="ux",
                    severity="medium",
                    title="Repeated clicks on same element indicates friction",
                    description=f"The agent clicked element '{eid}' {count} times without advancing the journey.",
                    evidence=f"Element {eid} received {count} clicks across trajectory.",
                    recommendation="Ensure the control provides immediate visual feedback (loading spinner, disabled state) or that its click handler functions reliably.",
                    source="heuristic",
                )
            )

    # 2. UI State loops (navigation loops)
    state_counts: dict[str, int] = {}
    for item in trajectory:
        state = item.get("state_signature")
        if state:
            state_counts[state] = state_counts.get(state, 0) + 1

    loops = [s for s, c in state_counts.items() if c >= 2]
    if loops:
        findings.append(
            Issue(
                id="nav-loop-detected",
                category="navigation",
                severity="medium",
                title="Navigation loop / cyclical state detected",
                description="The agent revisited identical observable UI states multiple times during exploration.",
                evidence=f"Revisited state signatures: {len(loops)} unique states cycled.",
                recommendation="Review user flow architecture to prevent circular dead-ends and clarify navigational hierarchy.",
                source="heuristic",
            )
        )

    # 3. Missing Accessible Names (WCAG 4.1.2 Name, Role, Value)
    for idx, item in enumerate(accessibility):
        if item.get("type") == "missing_name":
            tag = item.get("tag", "element")
            sel = item.get("selector", "")
            findings.append(
                Issue(
                    id=f"a11y-missing-name-{idx}",
                    category="accessibility",
                    severity="high",
                    title=f"Interactive <{tag}> has no accessible name",
                    description="An interactive control lacks visible text or an aria-label, rendering it unidentifiable to assistive technologies.",
                    evidence=f"Selector: {sel} | HTML: {item.get('html', '')[:160]}",
                    recommendation=f"Add aria-label='...' or descriptive inner text to the <{tag}> element.",
                    source="heuristic",
                )
            )

        # 4. Small touch target size (WCAG 2.5.5 / 2.5.8 Target Size)
        elif item.get("type") == "small_target":
            w = item.get("width", 0)
            h = item.get("height", 0)
            txt = item.get("text", "control")
            findings.append(
                Issue(
                    id=f"a11y-target-size-{idx}",
                    category="accessibility",
                    severity="medium",
                    title="Touch target smaller than 44×44px minimum",
                    description=f"Control '{txt}' measures {w}×{h}px, below the recommended 44×44px touch target guideline.",
                    evidence=f"Actual dimensions: {w}px wide by {h}px high.",
                    recommendation="Increase button/link padding or set CSS min-width: 44px; min-height: 44px; to improve mobile touch accuracy.",
                    source="heuristic",
                )
            )

        # 5. Low color contrast (WCAG 1.4.3 Contrast Minimum)
        elif item.get("type") == "low_contrast":
            ratio = item.get("ratio", 1.0)
            txt = item.get("text", "")
            findings.append(
                Issue(
                    id=f"a11y-contrast-{idx}",
                    category="accessibility",
                    severity="high" if ratio < 3.0 else "medium",
                    title="Insufficient text color contrast (WCAG AA)",
                    description=f"Text '{txt}' has a contrast ratio of {ratio}:1, failing WCAG 2.1 AA minimum threshold of 4.5:1.",
                    evidence=f"Contrast ratio: {ratio}:1 (Foreground: {item.get('color')}, Background: {item.get('bgColor')})",
                    recommendation="Darken the text color or lighten the background to reach a contrast ratio of at least 4.5:1.",
                    source="heuristic",
                )
            )

    # 6. Occlusion / Popups blocking actions (UX Friction / Visual)
    for idx, item in enumerate(layout):
        if item.get("type") == "occluded_control":
            findings.append(
                Issue(
                    id=f"visual-occlusion-{idx}",
                    category="visual",
                    severity="high",
                    title="Interactive element occluded by overlay/modal",
                    description="The center point of an interactive control is covered by another visible element, blocking user clicks.",
                    evidence=f"Target: '{item.get('text', 'unnamed')}' | Occluded by: {item.get('top', 'unknown')}",
                    recommendation="Ensure modals and overlays are dismissible, do not trigger unexpectedly, and use pointer-events: none on non-interactive backdrops.",
                    source="heuristic",
                )
            )

    # 7. High interaction depth (Path length friction)
    interactive_steps = len([x for x in trajectory if x.get("action") in {"click", "type", "scroll", "press_key", "back"}])
    if interactive_steps >= 12:
        findings.append(
            Issue(
                id="high-interaction-depth",
                category="ux",
                severity="low",
                title="Excessive interaction depth to complete goal",
                description=f"The journey required {interactive_steps} manual actions. High interaction depth correlates with customer drop-off.",
                evidence=f"{interactive_steps} interactive steps taken.",
                recommendation="Provide direct shortcuts, prominent search/filter presets, or single-click checkout options to reduce cognitive load.",
                source="heuristic",
            )
        )

    return findings


def calculate_metrics(
    trajectory: list[dict[str, Any]],
    issues: list[Issue],
    goal_completed: bool,
    paths_discovered: int,
) -> dict[str, Any]:
    """Calculate composite UX friction index and accessibility compliance score."""
    # Friction score (0 = flawless, 100 = critical friction)
    friction = 0
    if not goal_completed:
        friction += 35
    for issue in issues:
        if issue.category in {"ux", "navigation", "visual"}:
            if issue.severity == "critical":
                friction += 25
            elif issue.severity == "high":
                friction += 15
            elif issue.severity == "medium":
                friction += 8
            elif issue.severity == "low":
                friction += 4

    # Repetitive action penalty
    stuck_events = len([t for t in trajectory if t.get("stuck") or not t.get("action_result", {}).get("ok", True)])
    friction += min(20, stuck_events * 5)
    friction_score = min(100, max(0, friction))

    # Accessibility score (100 = perfect, 0 = broken)
    a11y_score = 100
    for issue in issues:
        if issue.category == "accessibility":
            if issue.severity == "critical":
                a11y_score -= 20
            elif issue.severity == "high":
                a11y_score -= 12
            elif issue.severity == "medium":
                a11y_score -= 6
            elif issue.severity == "low":
                a11y_score -= 3
    a11y_score = max(0, min(100, a11y_score))

    # WCAG Grade
    if a11y_score >= 90:
        wcag_grade = "AA (Compliant)"
    elif a11y_score >= 75:
        wcag_grade = "A (Acceptable)"
    elif a11y_score >= 50:
        wcag_grade = "Needs Improvement"
    else:
        wcag_grade = "Non-Compliant"

    return {
        "steps": len(trajectory),
        "paths": paths_discovered,
        "goal_completed": goal_completed,
        "friction_score": friction_score,
        "accessibility_score": a11y_score,
        "wcag_grade": wcag_grade,
        "accessibility_findings": len([i for i in issues if i.category == "accessibility"]),
        "ux_findings": len([i for i in issues if i.category == "ux"]),
        "navigation_findings": len([i for i in issues if i.category == "navigation"]),
        "visual_findings": len([i for i in issues if i.category == "visual"]),
    }
