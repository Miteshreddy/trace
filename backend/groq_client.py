from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Any
from PIL import Image
from openai import OpenAI, RateLimitError, BadRequestError

from .config import GROQ_API_KEY, GROQ_MODEL


class GroqAgentClient:
    def __init__(self) -> None:
        if not GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY is not configured. Add a fresh key to .env and restart the app."
            )
        self.client = OpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = GROQ_MODEL

    @staticmethod
    def _optimize_and_encode_image(image_bytes: bytes, max_dim: int = 480) -> str:
        """Resize and compress screenshot to minimize vision token footprint and latency."""
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_dim:
                    scale = max_dim / max(w, h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70, optimize=True)
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _parse_json_response(content: str) -> dict[str, Any]:
        """Robustly parse JSON even if surrounded by markdown code blocks or commentary."""
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass
        return {}

    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["click", "type", "scroll", "press_key", "back", "wait", "finish", "fail", "dismiss_modal"],
                },
                "element_id": {"type": ["string", "null"]},
                "text": {"type": ["string", "null"]},
                "key": {"type": ["string", "null"]},
                "scroll_y": {"type": ["integer", "null"]},
                "rationale": {"type": "string"},
                "expected_result": {"type": "string"},
                "confidence": {"type": "number"},
                "goal_complete": {"type": "boolean"},
                "stuck": {"type": "boolean"},
            },
            "required": [
                "action",
                "element_id",
                "text",
                "key",
                "scroll_y",
                "rationale",
                "expected_result",
                "confidence",
                "goal_complete",
                "stuck",
            ],
        }

        system = (
            "You are the visual navigation brain of TRACE//QA, an autonomous black-box UI testing agent.\n"
            "Your objective is to accomplish the user goal on the target interface like a human user.\n"
            "Rules:\n"
            "1. Inspect screenshot & visible UI controls (`ui_map`). Every element has an id (e.g. 'e0').\n"
            "2. Always pick a valid `element_id` from `ui_map` when clicking or typing.\n"
            "3. If a modal/popup blocks the view, click its close ('×') or continue button.\n"
            "4. If searching, type into search input and press Enter or click Search.\n"
            "5. If filtering, click the relevant filter button.\n"
            "6. To add a shoe to cart, click its 'Add to cart' button.\n"
            "7. When the user goal is completely achieved, set action='finish' and goal_complete=true.\n"
            "Output strictly valid JSON matching the schema."
        )

        # Keep payload token-efficient
        payload = {
            "goal": goal,
            "path_hint": path_hint,
            "ui_map": [
                {"id": e["id"], "tag": e["tag"], "role": e["role"], "text": e["text"], "occluded": e.get("occluded", False)}
                for e in ui_map[:35]
            ],
            "accessibility_tree": ax_tree[:25],
            "recent_actions": [
                {"step": h.get("step"), "action": h.get("action"), "element_id": h.get("element_id")}
                for h in history[-4:]
            ],
        }

        user_text = (
            f"User Goal: {goal}\n"
            + (f"Hint: {path_hint}\n" if path_hint else "")
            + json.dumps(payload, ensure_ascii=False)
        )

        optimized_image = self._optimize_and_encode_image(screenshot, max_dim=480)

        # Execute with retry on rate limit (ITPM/OTPM limits on free tier)
        for attempt in range(3):
            try:
                # On final retry if rate limit occurred, fallback to text-only observation
                use_vision = (attempt < 2)
                messages = [{"role": "system", "content": system}]

                if use_vision:
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": optimized_image}},
                        ],
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": user_text,
                    })

                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.1,
                    max_tokens=220,  # Keep low to prevent OTPM exhaustion
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "ui_action",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    messages=messages,
                )
                content = response.choices[0].message.content or "{}"
                parsed = self._parse_json_response(content)
                if parsed and "action" in parsed:
                    return parsed
            except RateLimitError:
                # Rate limit exceeded: wait 4s and retry
                time.sleep(4.0)
            except Exception as e:
                if "413" in str(e) or "rate_limit" in str(e).lower():
                    time.sleep(4.0)
                else:
                    break

        # Safe fallback action if all retries exhausted
        return {
            "action": "wait",
            "element_id": None,
            "text": None,
            "key": None,
            "scroll_y": None,
            "rationale": "Waiting for observation rate limit recovery",
            "expected_result": "Re-observe page",
            "confidence": 0.5,
            "goal_complete": False,
            "stuck": False,
        }

    def audit_run(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "friction_rating": {"type": "string", "enum": ["Low", "Moderate", "High", "Critical"]},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["accessibility", "ux", "navigation", "visual", "regression"],
                            },
                            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "evidence": {"type": "string"},
                            "recommendation": {"type": "string"},
                            "step": {"type": ["integer", "null"]},
                        },
                        "required": [
                            "category",
                            "severity",
                            "title",
                            "description",
                            "evidence",
                            "recommendation",
                            "step",
                        ],
                    },
                },
            },
            "required": ["summary", "friction_rating", "findings"],
        }

        system = (
            "You are a Senior Staff Product Accessibility & UX Auditor reviewing TRACE//QA findings.\n"
            "Analyze the trajectory and heuristic findings.\n"
            "Provide an executive summary and detailed issues with concrete, code-level remediation recommendations.\n"
            "Only report issues supported by the evidence."
        )

        user_content = json.dumps(
            {
                "goal": goal,
                "trajectory_summary": [
                    {"step": t.get("step"), "action": t.get("action"), "element": t.get("element_id"), "rationale": t.get("rationale")}
                    for t in trajectory[-10:]
                ],
                "heuristic_findings": heuristic_findings[:10],
            },
            ensure_ascii=False,
        )

        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.1,
                    max_tokens=420,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "audit_report",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                )
                content = response.choices[0].message.content or "{}"
                return self._parse_json_response(content)
            except Exception:
                time.sleep(3.0)

        return {
            "summary": f"Autonomous audit completed for goal: '{goal}'. Issues detected via automated heuristics.",
            "friction_rating": "Moderate",
            "findings": [],
        }
