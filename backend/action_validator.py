from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("traceqa.action_validator")

ALLOWED_ACTIONS = {
    "click",
    "type",
    "scroll",
    "press_key",
    "back",
    "wait",
    "finish",
    "fail",
    "retry",
    "dismiss_modal",
}

ALLOWED_KEYS = {
    "Enter",
    "Tab",
    "Escape",
    "ArrowDown",
    "ArrowUp",
    "ArrowLeft",
    "ArrowRight",
    "Backspace",
    "Space",
    "PageDown",
    "PageUp",
}


class ActionValidator:
    """Validates and sanitizes model-generated decisions before browser execution."""

    @staticmethod
    def validate(
        raw_decision: Any,
        ui_map: list[dict[str, Any]],
        viewport_w: int = 1440,
        viewport_h: int = 900,
    ) -> tuple[bool, dict[str, Any], str]:
        if not isinstance(raw_decision, dict):
            return False, {
                "action": "wait",
                "element_id": None,
                "text": None,
                "key": None,
                "scroll_y": None,
                "rationale": "Malformed model output (not a dictionary).",
                "expected_result": "Re-observe",
                "confidence": 0.0,
                "goal_complete": False,
                "stuck": True,
            }, "Raw model output was not a JSON dictionary."

        action = str(raw_decision.get("action", "")).strip().lower()

        # Map 'retry' to 'wait' with stuck flag
        if action == "retry":
            action = "wait"
            raw_decision["stuck"] = True

        if action not in ALLOWED_ACTIONS:
            return False, {
                "action": "wait",
                "element_id": None,
                "text": None,
                "key": None,
                "scroll_y": None,
                "rationale": f"Unsafe or unrecognized action '{action}'.",
                "expected_result": "Re-observe interface",
                "confidence": 0.0,
                "goal_complete": False,
                "stuck": True,
            }, f"Action '{action}' is not in allowed actions list."

        valid_eids = {e.get("id") for e in ui_map if e.get("id")}

        # 1. Validate 'click'
        if action == "click":
            eid = raw_decision.get("element_id")
            x = raw_decision.get("x")
            y = raw_decision.get("y")

            if not eid and (x is None or y is None):
                return False, {
                    "action": "wait",
                    "element_id": None,
                    "rationale": "Click rejected: missing element_id or coordinates.",
                    "confidence": 0.2,
                    "goal_complete": False,
                    "stuck": True,
                }, "Click specified without a target element_id or (x, y) coordinates."

            if eid and eid not in valid_eids:
                # If element ID doesn't exist, check if (x, y) is provided or fallback
                if x is not None and y is not None:
                    raw_decision["element_id"] = None
                else:
                    return False, {
                        "action": "wait",
                        "element_id": None,
                        "rationale": f"Click rejected: element_id '{eid}' does not exist on current page.",
                        "confidence": 0.2,
                        "goal_complete": False,
                        "stuck": True,
                    }, f"Target element_id '{eid}' not found in observable DOM map."

            # Coordinate bounds check
            if x is not None and y is not None:
                if not (0 <= x <= viewport_w * 2 and 0 <= y <= viewport_h * 3):
                    return False, {
                        "action": "wait",
                        "rationale": "Click coordinates out of bounds.",
                        "confidence": 0.1,
                    }, f"Coordinates ({x}, {y}) exceed viewport bounds."

        # 2. Validate 'type'
        elif action == "type":
            eid = raw_decision.get("element_id")
            text = str(raw_decision.get("text", "") or "")

            if not eid or eid not in valid_eids:
                return False, {
                    "action": "wait",
                    "element_id": None,
                    "rationale": f"Type rejected: invalid target input element '{eid}'.",
                    "confidence": 0.2,
                    "goal_complete": False,
                    "stuck": True,
                }, f"Input element '{eid}' not found in observable DOM map."

            # Sanitize text: prevent massive text floods or script tags
            if len(text) > 400:
                raw_decision["text"] = text[:400]
            if "<script" in text.lower() or "javascript:" in text.lower():
                return False, {
                    "action": "wait",
                    "rationale": "Type action contained disallowed script patterns.",
                }, "Disallowed script pattern detected in input text."

        # 3. Validate 'scroll'
        elif action == "scroll":
            scroll_val = raw_decision.get("scroll_y") or raw_decision.get("amount") or 450
            try:
                clamped = max(-2000, min(2000, int(scroll_val)))
                raw_decision["scroll_y"] = clamped
            except (ValueError, TypeError):
                raw_decision["scroll_y"] = 450

        # 4. Validate 'press_key'
        elif action == "press_key":
            key = str(raw_decision.get("key", "Enter")).strip()
            if key not in ALLOWED_KEYS:
                raw_decision["key"] = "Enter"

        # 5. Normalize fields
        normalized = {
            "action": action,
            "element_id": raw_decision.get("element_id"),
            "text": raw_decision.get("text"),
            "key": raw_decision.get("key"),
            "scroll_y": raw_decision.get("scroll_y"),
            "rationale": str(raw_decision.get("rationale", "")).strip(),
            "expected_result": str(raw_decision.get("expected_result", "")).strip(),
            "confidence": max(0.0, min(1.0, float(raw_decision.get("confidence", 0.5)))),
            "goal_complete": bool(raw_decision.get("goal_complete", False)),
            "stuck": bool(raw_decision.get("stuck", False)),
        }

        return True, normalized, "Action passed validation."
