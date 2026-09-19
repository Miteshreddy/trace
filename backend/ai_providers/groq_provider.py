from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Any
from PIL import Image
from openai import AsyncOpenAI, OpenAI, RateLimitError

from ..config import GROQ_API_KEY, GROQ_API_KEY_2, GROQ_BASE_URL, GROQ_MODEL
from .base import AIProvider

# Models that support vision (image) input on Groq
_VISION_CAPABLE_GROQ_MODELS = {
    "llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-4-maverick",
}


class GroqProvider(AIProvider):
    def __init__(self) -> None:
        super().__init__(name="groq", model=GROQ_MODEL)
        self.api_key = GROQ_API_KEY
        self.api_key_2 = GROQ_API_KEY_2
        self.base_url = GROQ_BASE_URL
        self.client: OpenAI | None = None
        self.client_2: OpenAI | None = None
        self.async_client: AsyncOpenAI | None = None
        self.async_client_2: AsyncOpenAI | None = None
        # Determine if this model supports image input
        self.supports_vision: bool = any(
            k in GROQ_MODEL.lower() for k in ["llama-4-scout", "llama-4-maverick"]
        )

        if self.is_configured():
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, max_retries=0)
            self.async_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=8.0, max_retries=0)
            self.health.configured = True
            self.health.status = "healthy"
        else:
            self.health.configured = False
            self.health.status = "unconfigured"

        # Secondary key client for rate-limit failover
        if self.api_key_2 and len(self.api_key_2) > 5:
            self.client_2 = OpenAI(api_key=self.api_key_2, base_url=self.base_url, max_retries=0)
            self.async_client_2 = AsyncOpenAI(api_key=self.api_key_2, base_url=self.base_url, timeout=8.0, max_retries=0)

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    @staticmethod
    def _optimize_and_encode_image(image_bytes: bytes, max_dim: int = 480) -> str:
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                w, h = img.size
                # Ensure minimum dimension is >= 32px for vision models
                if w < 32 or h < 32:
                    scale_up = max(32 / max(w, 1), 32 / max(h, 1))
                    img = img.resize((int(w * scale_up), int(h * scale_up)), Image.Resampling.NEAREST)
                    w, h = img.size
                if max(w, h) > max_dim:
                    scale = max_dim / max(w, h)
                    img = img.resize((max(32, int(w * scale)), max(32, int(h * scale))), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70, optimize=True)
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"
        except Exception:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _parse_json_response(content: str) -> dict[str, Any]:
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

    def ping(self) -> tuple[bool, float, str]:
        if not self.is_configured() or not self.client:
            return False, 0.0, "API key not configured"
        start = time.perf_counter()
        try:
            self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=10,
                timeout=8.0,
            )
            lat = (time.perf_counter() - start) * 1000.0
            self.record_success(lat)
            return True, lat, "Connected"
        except Exception as e:
            lat = (time.perf_counter() - start) * 1000.0
            err = str(e)
            self.record_failure(err)
            return False, lat, err[:100]

    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        if not self.is_configured() or not self.client:
            raise RuntimeError("Groq provider is not configured.")

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
            "You are the autonomous browser controller for TRACE//QA, a UI testing agent.\n"
            "Your objective is to accomplish the user goal on the target interface.\n"
            "Rules:\n"
            "1. Inspect visible UI controls in `ui_map`. Every element has an id (e.g. 'e0').\n"
            "2. Always pick a valid `element_id` from `ui_map` when clicking or typing.\n"
            "3. If a modal/popup blocks the view, use action='dismiss_modal'.\n"
            "4. If searching, type into search input then press_key Enter.\n"
            "5. When the user goal is fully achieved, set action='finish' and goal_complete=true.\n"
            "6. If no progress after 3 same actions, set stuck=true and try a different approach.\n"
            "Output strictly valid JSON matching the schema."
        )

        payload = {
            "goal": goal,
            "path_hint": path_hint,
            "ui_map": [
                {"id": e["id"], "tag": e["tag"], "role": e["role"], "text": e["text"], "occluded": e.get("occluded", False)}
                for e in ui_map[:35]
            ],
            "accessibility_tree": ax_tree[:25],
            "recent_actions": [
                {"step": h.get("step"), "action": h.get("action"), "element_id": h.get("element_id"), "result_ok": h.get("action_result", {}).get("ok", True)}
                for h in history[-4:]
            ],
        }

        user_text = (
            f"User Goal: {goal}\n"
            + (f"Hint: {path_hint}\n" if path_hint else "")
            + json.dumps(payload, ensure_ascii=False)
        )

        start_time = time.perf_counter()

        # Try primary key first, then secondary key on rate limit
        clients_to_try = [c for c in [self.client, self.client_2] if c is not None]
        if not clients_to_try:
            raise RuntimeError("No Groq client available")

        last_err: Exception | None = None
        for client_idx, active_client in enumerate(clients_to_try):
            for attempt in range(2):
                try:
                    # Attempt 0: use vision if model supports it
                    # Attempt 1: text-only fallback
                    # For non-vision models: always text-only
                    use_vision = (attempt == 0) and self.supports_vision

                    messages = [{"role": "system", "content": system}]
                    if use_vision:
                        optimized_image = self._optimize_and_encode_image(screenshot, max_dim=480)
                        messages.append({
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_text},
                                {"type": "image_url", "image_url": {"url": optimized_image}},
                            ],
                        })
                    else:
                        messages.append({"role": "user", "content": user_text})

                    response = active_client.chat.completions.create(
                        model=self.model,
                        temperature=0.1,
                        max_tokens=250,
                        timeout=18.0,
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
                    lat = (time.perf_counter() - start_time) * 1000.0
                    self.record_success(lat)
                    content = response.choices[0].message.content or "{}"
                    parsed = self._parse_json_response(content)
                    if parsed and parsed.get("action"):
                        return parsed
                    # If empty/invalid, try text-only next
                    if use_vision:
                        continue
                    # Already text-only and still failed — raise
                    raise RuntimeError("Model returned empty/invalid action JSON")

                except RateLimitError as rle:
                    last_err = rle
                    if attempt == 0 and self.supports_vision:
                        # Try text-only mode next
                        continue
                    else:
                        # Exhausted attempts for this client
                        break
                except Exception as e:
                    lat = (time.perf_counter() - start_time) * 1000.0
                    self.record_failure(str(e))
                    raise

        # All clients exhausted on rate limit
        lat = (time.perf_counter() - start_time) * 1000.0
        self.record_failure(str(last_err))
        raise last_err  # type: ignore[misc]

    def audit_run(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.is_configured() or not self.client:
            raise RuntimeError("Groq provider is not configured.")

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
                    {"step": t.get("step"), "action": t.get("action"), "element": t.get("element_id"), "rationale": t.get("rationale"), "ok": t.get("action_result", {}).get("ok", True)}
                    for t in trajectory[-10:]
                ],
                "heuristic_findings": heuristic_findings[:10],
            },
            ensure_ascii=False,
        )

        start_time = time.perf_counter()
        # Use secondary key if primary has been rate-limited recently
        client_to_use = self.client
        if self.client_2 and self.health.failures > 0 and self.health.last_error and "rate" in self.health.last_error.lower():
            client_to_use = self.client_2

        try:
            response = client_to_use.chat.completions.create(  # type: ignore[union-attr]
                model=self.model,
                temperature=0.1,
                max_tokens=500,
                timeout=22.0,
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
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            content = response.choices[0].message.content or "{}"
            return self._parse_json_response(content)
        except Exception as e:
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_failure(str(e))
            raise

    async def decide_action_async(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        if not self.is_configured() or not self.async_client:
            raise RuntimeError("Groq provider is not configured.")

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
            "You are the autonomous browser controller for TRACE//QA, a UI testing agent.\n"
            "Your objective is to accomplish the user goal on the target interface.\n"
            "Rules:\n"
            "1. Inspect visible UI controls in `ui_map`. Every element has an id (e.g. 'e0').\n"
            "2. Always pick a valid `element_id` from `ui_map` when clicking or typing.\n"
            "3. If a modal/popup blocks the view, use action='dismiss_modal'.\n"
            "4. If searching, type into search input then press_key Enter.\n"
            "5. When the user goal is fully achieved, set action='finish' and goal_complete=true.\n"
            "6. If no progress after 3 same actions, set stuck=true and try a different approach.\n"
            "Output strictly valid JSON matching the schema."
        )

        payload = {
            "goal": goal,
            "path_hint": path_hint,
            "ui_map": [
                {"id": e["id"], "tag": e["tag"], "role": e["role"], "text": e["text"], "occluded": e.get("occluded", False)}
                for e in ui_map[:35]
            ],
            "accessibility_tree": ax_tree[:25],
            "recent_actions": [
                {"step": h.get("step"), "action": h.get("action"), "element_id": h.get("element_id"), "result_ok": h.get("action_result", {}).get("ok", True)}
                for h in history[-4:]
            ],
        }

        user_text = (
            f"User Goal: {goal}\n"
            + (f"Hint: {path_hint}\n" if path_hint else "")
            + json.dumps(payload, ensure_ascii=False)
        )

        start_time = time.perf_counter()
        clients_to_try = [c for c in [self.async_client, self.async_client_2] if c is not None]
        if not clients_to_try:
            raise RuntimeError("No Groq async client available")

        last_err: Exception | None = None
        for active_client in clients_to_try:
            for attempt in range(2):
                try:
                    use_vision = (attempt == 0) and self.supports_vision
                    messages = [{"role": "system", "content": system}]
                    if use_vision:
                        optimized_image = self._optimize_and_encode_image(screenshot, max_dim=480)
                        messages.append({
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_text},
                                {"type": "image_url", "image_url": {"url": optimized_image}},
                            ],
                        })
                    else:
                        messages.append({"role": "user", "content": user_text})

                    response = await active_client.chat.completions.create(
                        model=self.model,
                        temperature=0.1,
                        max_tokens=250,
                        timeout=10.0,
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
                    lat = (time.perf_counter() - start_time) * 1000.0
                    self.record_success(lat)
                    content = response.choices[0].message.content or "{}"
                    parsed = self._parse_json_response(content)
                    if parsed and parsed.get("action"):
                        return parsed
                    if use_vision:
                        continue
                    raise RuntimeError("Model returned empty/invalid action JSON")

                except RateLimitError as rle:
                    last_err = rle
                    if attempt == 0 and self.supports_vision:
                        continue
                    break
                except Exception as e:
                    last_err = e
                    if attempt == 0 and self.supports_vision:
                        continue
                    break

        lat = (time.perf_counter() - start_time) * 1000.0
        self.record_failure(str(last_err))
        raise last_err or RuntimeError("Groq async decision failed")

    async def audit_run_async(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.is_configured() or not self.async_client:
            raise RuntimeError("Groq provider is not configured.")

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
                    {"step": t.get("step"), "action": t.get("action"), "element": t.get("element_id"), "rationale": t.get("rationale"), "ok": t.get("action_result", {}).get("ok", True)}
                    for t in trajectory[-10:]
                ],
                "heuristic_findings": heuristic_findings[:10],
            },
            ensure_ascii=False,
        )

        start_time = time.perf_counter()
        client_to_use = self.async_client
        if self.async_client_2 and self.health.failures > 0 and self.health.last_error and "rate" in self.health.last_error.lower():
            client_to_use = self.async_client_2

        try:
            response = await client_to_use.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=500,
                timeout=15.0,
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
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            content = response.choices[0].message.content or "{}"
            return self._parse_json_response(content)
        except Exception as e:
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_failure(str(e))
            raise
