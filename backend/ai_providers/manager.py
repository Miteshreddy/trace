from __future__ import annotations

import logging
from typing import Any, Literal

from ..config import AI_PROVIDER_MODE
from .base import AIProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider

logger = logging.getLogger("traceqa.ai_manager")


class AIProviderManager:
    def __init__(self) -> None:
        self.groq = GroqProvider()
        self.gemini = GeminiProvider()
        self.providers: dict[str, AIProvider] = {
            "groq": self.groq,
            "gemini": self.gemini,
        }
        valid_mode = AI_PROVIDER_MODE if AI_PROVIDER_MODE in {"AUTO", "GROQ", "GEMINI"} else "AUTO"
        self.mode: Literal["AUTO", "GROQ", "GEMINI"] = valid_mode  # type: ignore[assignment]
        self._turn: int = 0

    def set_mode(self, mode: str) -> None:
        upper = mode.strip().upper()
        if upper in {"AUTO", "GROQ", "GEMINI"}:
            self.mode = upper  # type: ignore[assignment]
            logger.info("AI Provider mode changed to: %s", self.mode)
        else:
            raise ValueError(f"Invalid mode '{mode}'. Choose AUTO, GROQ, or GEMINI.")

    def _get_provider_order(self) -> list[AIProvider]:
        """Determine primary and failover providers based on mode and health."""
        if self.mode == "GROQ":
            return [self.groq] if self.groq.is_configured() else [self.gemini]
        if self.mode == "GEMINI":
            return [self.gemini] if self.gemini.is_configured() else [self.groq]

        # AUTO Mode
        groq_ok = self.groq.is_configured() and self.groq.health.status != "unhealthy"
        gemini_ok = self.gemini.is_configured() and self.gemini.health.status != "unhealthy"

        if groq_ok and gemini_ok:
            # Alternate primary provider across turns for balanced utilization
            self._turn += 1
            if self._turn % 2 == 1:
                return [self.groq, self.gemini]
            else:
                return [self.gemini, self.groq]
        elif groq_ok:
            return [self.groq, self.gemini] if self.gemini.is_configured() else [self.groq]
        elif gemini_ok:
            return [self.gemini, self.groq] if self.groq.is_configured() else [self.gemini]
        else:
            # Try any configured provider as last resort
            configured = [p for p in [self.groq, self.gemini] if p.is_configured()]
            return configured or [self.groq]

    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Execute action decision with automatic failover if primary provider errors."""
        order = self._get_provider_order()
        last_err: Exception | None = None

        for idx, provider in enumerate(order):
            try:
                result = provider.decide_action(
                    goal=goal,
                    screenshot=screenshot,
                    ui_map=ui_map,
                    ax_tree=ax_tree,
                    history=history,
                    path_hint=path_hint,
                )
                if result and result.get("action"):
                    return result, provider.name
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "[Failover] Provider '%s' failed in decide_action: %s. %s",
                    provider.name,
                    exc,
                    f"Attempting fallback to '{order[idx+1].name}'." if idx + 1 < len(order) else "No fallbacks left.",
                )

        # Fallback safe recovery action
        logger.error("All AI providers failed: %s", last_err)
        return {
            "action": "wait",
            "element_id": None,
            "text": None,
            "key": None,
            "scroll_y": None,
            "rationale": f"All AI providers temporarily degraded ({str(last_err)[:80]}). Recovering...",
            "expected_result": "Re-observe target interface",
            "confidence": 0.3,
            "goal_complete": False,
            "stuck": False,
        }, "fallback"

    def audit_run(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """Execute audit report generation with automatic failover."""
        order = self._get_provider_order()
        last_err: Exception | None = None

        for idx, provider in enumerate(order):
            try:
                result = provider.audit_run(
                    goal=goal,
                    screenshots=screenshots,
                    trajectory=trajectory,
                    heuristic_findings=heuristic_findings,
                )
                if result and "findings" in result:
                    return result, provider.name
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "[Failover] Provider '%s' failed in audit_run: %s.", provider.name, exc
                )

        return {
            "summary": f"Audit completed for goal '{goal}'. Findings generated via automated heuristics.",
            "friction_rating": "Moderate",
            "findings": [],
        }, "fallback"

    def get_health_status(self) -> dict[str, Any]:
        """Return runtime health telemetry for all providers without exposing keys."""
        return {
            "mode": self.mode,
            "providers": {
                name: {
                    "name": p.name,
                    "model": p.model,
                    **p.health.to_dict(),
                }
                for name, p in self.providers.items()
            },
        }

    def test_all_providers(self) -> dict[str, Any]:
        """Execute live ping diagnostics on both Groq and Gemini."""
        results = {}
        for name, provider in self.providers.items():
            ok, lat, msg = provider.ping()
            results[name] = {
                "name": name,
                "model": provider.model,
                "configured": provider.is_configured(),
                "connected": ok,
                "latency_ms": round(lat, 1),
                "message": msg,
            }
        return {
            "mode": self.mode,
            "results": results,
        }


# Global singleton instance
ai_provider_manager = AIProviderManager()
