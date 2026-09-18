from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Any
from PIL import Image
from openai import OpenAI

from ..config import GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL
from .base import AIProvider


class GeminiProvider(AIProvider):
    def __init__(self) -> None:
        super().__init__(name="gemini", model=GEMINI_MODEL)
        self.api_key = GEMINI_API_KEY
        self.base_url = GEMINI_BASE_URL
        self.client: OpenAI | None = None

        if self.is_configured():
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            self.health.configured = True
            self.health.status = "healthy"
        else:
            self.health.configured = False
            self.health.status = "unconfigured"

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    @staticmethod
    def _optimize_and_encode_image(image_bytes: bytes, max_dim: int = 640) -> str:
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_dim:
                    scale = max_dim / max(w, h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75, optimize=True)
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
            res = self.client.chat.completions.create(
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
            raise RuntimeError("Gemini provider is not configured.")

        system = (
            "You are the visual navigation brain of TRACE//QA, an autonomous black-box UI testing agent.\n"
            "Your objective is to accomplish the user goal on the target interface like a human user.\n"
            "CRITICAL RULES:\n"
            "1. Inspect screenshot & visible UI controls (`ui_map`). Every element has an id (e.g. 'e0').\n"
            "2. Always pick a valid `element_id` from `ui_map` when clicking or typing.\n"
            "3. If a modal/popup blocks the view, click its close ('×') or continue button.\n"
            "4. If searching, type into search input and press Enter or click Search.\n"
            "5. If filtering, click the relevant filter button.\n"
            "6. To add a shoe to cart, click its 'Add to cart' button.\n"
            "7. When the user goal is completely achieved, set action='finish' and goal_complete=true.\n"
            "Respond ONLY with a JSON object having keys:\n"
            '{"action":"click"|"type"|"scroll"|"press_key"|"back"|"wait"|"finish"|"fail"|"dismiss_modal",\n'
            '"element_id":string|null, "text":string|null, "key":string|null, "scroll_y":number|null,\n'
            '"rationale":string, "expected_result":string, "confidence":number, "goal_complete":boolean, "stuck":boolean}'
        )

        payload = {
            "goal": goal,
            "path_hint": path_hint,
            "ui_map": [
                {"id": e["id"], "tag": e["tag"], "role": e["role"], "text": e["text"], "occluded": e.get("occluded", False)}
                for e in ui_map[:40]
            ],
            "accessibility_tree": ax_tree[:30],
            "recent_actions": [
                {"step": h.get("step"), "action": h.get("action"), "element_id": h.get("element_id")}
                for h in history[-5:]
            ],
        }

        user_text = (
            f"User Goal: {goal}\n"
            + (f"Exploration Hint: {path_hint}\n" if path_hint else "")
            + "Current page observation is attached as an image. Decide the next action.\n"
            + json.dumps(payload, ensure_ascii=False)
        )

        optimized_image = self._optimize_and_encode_image(screenshot, max_dim=640)
        start_time = time.perf_counter()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=400,
                timeout=18.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": optimized_image}},
                        ],
                    },
                ],
            )
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            content = response.choices[0].message.content or "{}"
            parsed = self._parse_json_response(content)
            if not parsed or "action" not in parsed:
                raise ValueError("Gemini returned invalid action schema.")
            return parsed
        except Exception as e:
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_failure(str(e))
            raise

    def audit_run(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.is_configured() or not self.client:
            raise RuntimeError("Gemini provider is not configured.")

        system = (
            "You are a Senior Staff Product Accessibility & UX Auditor reviewing TRACE//QA findings.\n"
            "Analyze the trajectory and heuristic findings.\n"
            "Provide an executive summary and detailed issues with concrete, code-level remediation recommendations.\n"
            "Respond ONLY with a JSON object having keys:\n"
            '{"summary": string, "friction_rating": "Low"|"Moderate"|"High"|"Critical", "findings": ['
            '{"category": "accessibility"|"ux"|"navigation"|"visual"|"regression", "severity": "low"|"medium"|"high"|"critical", '
            '"title": string, "description": string, "evidence": string, "recommendation": string, "step": number|null}]}'
        )

        user_content = json.dumps(
            {
                "goal": goal,
                "trajectory_summary": [
                    {"step": t.get("step"), "action": t.get("action"), "element": t.get("element_id"), "rationale": t.get("rationale")}
                    for t in trajectory[-12:]
                ],
                "heuristic_findings": heuristic_findings[:12],
            },
            ensure_ascii=False,
        )

        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
        if screenshots:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": self._optimize_and_encode_image(screenshots[-1], max_dim=640)},
            })

        start_time = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=800,
                timeout=20.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content_blocks},
                ],
            )
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            content = response.choices[0].message.content or "{}"
            parsed = self._parse_json_response(content)
            if not parsed or "findings" not in parsed:
                return {
                    "summary": f"Audit completed for goal '{goal}'.",
                    "friction_rating": "Moderate",
                    "findings": [],
                }
            return parsed
        except Exception as e:
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_failure(str(e))
            raise
