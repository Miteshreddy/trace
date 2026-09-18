"""
Local Gemma Provider — Google Gemma 3 via Hugging Face Transformers.

This provider runs inference locally without any cloud calls.
Used in offline mode or as a fallback when all cloud providers are unavailable.

Model: google/gemma-3-1b-it (smallest usable Gemma 3 instruction-tuned)
       configurable via GOOGLE_LOCAL_MODEL env var.

Requirements (optional — only needed for local mode):
    pip install transformers torch accelerate
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from .base import AIProvider

logger = logging.getLogger("traceqa.local_gemma")

# Configurable via env
LOCAL_MODEL_ID = os.getenv("GOOGLE_LOCAL_MODEL", "google/gemma-3-1b-it")

_model = None   # Lazy-loaded model instance
_tokenizer = None
_pipeline = None
_load_attempted = False
_load_error: str = ""


def _try_load_model() -> bool:
    """Attempt to load the Gemma model. Returns True if successful."""
    global _model, _tokenizer, _pipeline, _load_attempted, _load_error

    if _load_attempted:
        return _pipeline is not None

    _load_attempted = True
    logger.info("[local_gemma] Attempting to load model: %s", LOCAL_MODEL_ID)

    try:
        import torch
        from transformers import pipeline as hf_pipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            logger.warning("[local_gemma] GPU not available — running on CPU. Inference will be slow.")
        else:
            logger.info("[local_gemma] GPU detected: %s", torch.cuda.get_device_name(0))

        _pipeline = hf_pipeline(
            "text-generation",
            model=LOCAL_MODEL_ID,
            device=device,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            max_new_tokens=300,
        )
        logger.info("[local_gemma] Model loaded successfully on %s", device.upper())
        return True

    except ImportError as e:
        _load_error = f"transformers/torch not installed: {e}"
        logger.warning("[local_gemma] Cannot load local model — %s", _load_error)
        logger.warning("[local_gemma] Install with: pip install transformers torch accelerate")
        return False
    except OSError as e:
        _load_error = f"Model not downloaded: {e}"
        logger.warning("[local_gemma] Model not found locally — %s", _load_error)
        logger.warning("[local_gemma] Download with: huggingface-cli download %s", LOCAL_MODEL_ID)
        return False
    except Exception as e:
        _load_error = str(e)[:200]
        logger.error("[local_gemma] Failed to load model: %s", _load_error)
        return False


def _run_inference(prompt: str, max_tokens: int = 300) -> str:
    """Run text generation on the loaded pipeline."""
    global _pipeline
    if _pipeline is None:
        raise RuntimeError(f"Local model not available: {_load_error}")
    try:
        result = _pipeline(
            prompt,
            max_new_tokens=max_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            return_full_text=False,
        )
        return result[0]["generated_text"].strip()
    except Exception as e:
        logger.error("[local_gemma] Inference error: %s", e)
        raise


class LocalGemmaProvider(AIProvider):
    """
    Offline-capable provider using Google Gemma 3 via Hugging Face Transformers.

    - No internet required after model download
    - No API keys needed
    - Supports text-only (no vision — Gemma 3 1B/4B are text models)
    - Loads model lazily on first use
    """

    def __init__(self) -> None:
        super().__init__(name="local_gemma", model=LOCAL_MODEL_ID)
        # Don't load at startup — lazy load on first use
        self.health.configured = False
        self.health.status = "unconfigured"

    def is_configured(self) -> bool:
        """Returns True if transformers + the model are available."""
        if not _load_attempted:
            return _try_load_model()
        return _pipeline is not None

    def ensure_loaded(self) -> bool:
        """Force load attempt if not yet tried."""
        return _try_load_model()

    def ping(self) -> tuple[bool, float, str]:
        start = time.perf_counter()
        if not self.ensure_loaded():
            return False, 0.0, f"Local model unavailable: {_load_error}"
        try:
            # Minimal test inference
            _run_inference("Hello", max_tokens=5)
            lat = (time.perf_counter() - start) * 1000.0
            self.record_success(lat)
            return True, lat, f"Local Gemma ready ({LOCAL_MODEL_ID})"
        except Exception as e:
            lat = (time.perf_counter() - start) * 1000.0
            self.record_failure(str(e))
            return False, lat, str(e)[:100]

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

    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> dict[str, Any]:
        if not self.ensure_loaded():
            raise RuntimeError(f"Local Gemma unavailable: {_load_error}")

        # Compact UI representation for small model
        compact_ui = [
            {"id": e["id"], "tag": e["tag"], "text": (e.get("text") or "")[:60]}
            for e in ui_map[:20]
        ]
        recent = [
            {"step": h.get("step"), "action": h.get("action"), "id": h.get("element_id")}
            for h in history[-3:]
        ]

        prompt = (
            f"<start_of_turn>user\n"
            f"You are a browser automation agent. Respond ONLY with JSON.\n"
            f"Goal: {goal}\n"
            f"UI elements: {json.dumps(compact_ui)}\n"
            f"Recent actions: {json.dumps(recent)}\n"
            + (f"Hint: {path_hint}\n" if path_hint else "")
            + "Choose the next action. JSON format: "
            '{"action":"click|type|scroll|press_key|back|wait|finish","element_id":"e0 or null","text":"text or null","key":"Enter or null","scroll_y":null,"rationale":"why","expected_result":"what","confidence":0.8,"goal_complete":false,"stuck":false}\n'
            "<end_of_turn>\n<start_of_turn>model\n"
        )

        start_time = time.perf_counter()
        try:
            raw = _run_inference(prompt, max_tokens=200)
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            parsed = self._parse_json_response(raw)
            if not parsed or "action" not in parsed:
                # Safe fallback
                return {
                    "action": "wait",
                    "element_id": None, "text": None, "key": None, "scroll_y": None,
                    "rationale": "Local model returned unparseable output. Waiting.",
                    "expected_result": "Re-observe", "confidence": 0.3,
                    "goal_complete": False, "stuck": True,
                }
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
        if not self.ensure_loaded():
            raise RuntimeError(f"Local Gemma unavailable: {_load_error}")

        compact_trajectory = [
            {"step": t.get("step"), "action": t.get("action"), "rationale": (t.get("rationale") or "")[:80]}
            for t in trajectory[-8:]
        ]

        prompt = (
            "<start_of_turn>user\n"
            f"You are a UX/accessibility auditor. Goal: {goal}\n"
            f"Trajectory: {json.dumps(compact_trajectory)}\n"
            f"Heuristic findings: {json.dumps(heuristic_findings[:5])}\n"
            "Produce a JSON audit report: "
            '{"summary":"...", "friction_rating":"Low|Moderate|High|Critical", "findings":['
            '{"category":"accessibility|ux|navigation|visual","severity":"low|medium|high|critical",'
            '"title":"...","description":"...","evidence":"...","recommendation":"...","step":null}'
            ']}\n'
            "<end_of_turn>\n<start_of_turn>model\n"
        )

        start_time = time.perf_counter()
        try:
            raw = _run_inference(prompt, max_tokens=400)
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_success(lat)
            parsed = self._parse_json_response(raw)
            if parsed and "findings" in parsed:
                return parsed
            # Minimal fallback
            return {
                "summary": f"Local offline audit completed for goal: {goal}.",
                "friction_rating": "Moderate",
                "findings": [
                    {
                        "category": f.get("category", "ux"),
                        "severity": f.get("severity", "medium"),
                        "title": f.get("type", "Issue detected"),
                        "description": str(f)[:200],
                        "evidence": "Detected via deterministic heuristic analysis.",
                        "recommendation": "Review and address the flagged element.",
                        "step": None,
                    }
                    for f in heuristic_findings[:5]
                ],
            }
        except Exception as e:
            lat = (time.perf_counter() - start_time) * 1000.0
            self.record_failure(str(e))
            raise
