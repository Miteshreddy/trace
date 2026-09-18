from __future__ import annotations

import logging
import os
from typing import Any, Literal

from ..config import AI_PROVIDER_MODE
from .base import AIProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .local_gemma import LocalGemmaProvider

logger = logging.getLogger("traceqa.ai_manager")

ProviderMode = Literal["AUTO", "GROQ", "GEMINI", "LOCAL", "OFFLINE"]


class AIProviderManager:
    def __init__(self) -> None:
        self.groq = GroqProvider()
        self.gemini = GeminiProvider()
        self.local_gemma = LocalGemmaProvider()
        self.providers: dict[str, AIProvider] = {
            "groq": self.groq,
            "gemini": self.gemini,
            "local_gemma": self.local_gemma,
        }
        valid_modes = {"AUTO", "GROQ", "GEMINI", "LOCAL", "OFFLINE"}
        raw_mode = AI_PROVIDER_MODE.upper() if AI_PROVIDER_MODE else "AUTO"
        self.mode: ProviderMode = raw_mode if raw_mode in valid_modes else "AUTO"  # type: ignore[assignment]
        self._turn: int = 0
        logger.info("[ai_manager] Initialized. Mode=%s Groq=%s Gemini=%s",
                    self.mode,
                    "configured" if self.groq.is_configured() else "not configured",
                    "configured" if self.gemini.is_configured() else "not configured")

    def set_mode(self, mode: str) -> None:
        upper = mode.strip().upper()
        valid = {"AUTO", "GROQ", "GEMINI", "LOCAL", "OFFLINE"}
        if upper in valid:
            self.mode = upper  # type: ignore[assignment]
            logger.info("[ai_manager] Mode changed to: %s", self.mode)
        else:
            raise ValueError(f"Invalid mode '{mode}'. Choose AUTO, GROQ, GEMINI, LOCAL, or OFFLINE.")

    def _get_provider_order(self) -> list[AIProvider]:
        """Determine provider priority order based on mode and health."""

        # Explicit LOCAL or OFFLINE → use only local Gemma
        if self.mode in ("LOCAL", "OFFLINE"):
            logger.info("[ai_manager] Local/Offline mode: using LocalGemmaProvider")
            return [self.local_gemma]

        if self.mode == "GROQ":
            candidates = [self.groq]
            if self.gemini.is_configured():
                candidates.append(self.gemini)
            candidates.append(self.local_gemma)
            return candidates

        if self.mode == "GEMINI":
            candidates = [self.gemini]
            if self.groq.is_configured():
                candidates.append(self.groq)
            candidates.append(self.local_gemma)
            return candidates

        # AUTO Mode: build healthy list, fall back to local
        groq_ok = self.groq.is_configured() and self.groq.health.status != "unhealthy"
        gemini_ok = self.gemini.is_configured() and self.gemini.health.status != "unhealthy"

        cloud_order: list[AIProvider] = []
        if groq_ok and gemini_ok:
            # Alternate for balanced load
            self._turn += 1
            if self._turn % 2 == 1:
                cloud_order = [self.groq, self.gemini]
            else:
                cloud_order = [self.gemini, self.groq]
        elif groq_ok:
            cloud_order = [self.groq]
            if self.gemini.is_configured():
                cloud_order.append(self.gemini)
        elif gemini_ok:
            cloud_order = [self.gemini]
            if self.groq.is_configured():
                cloud_order.append(self.groq)
        else:
            configured = [p for p in [self.groq, self.gemini] if p.is_configured()]
            cloud_order = configured

        # Always append local as final fallback
        cloud_order.append(self.local_gemma)
        return cloud_order

    def decide_action(
        self,
        goal: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Execute action decision with automatic failover through provider chain."""
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
                    logger.info("[ai_manager] Action from %s: %s", provider.name, result.get("action"))
                    return result, provider.name
            except Exception as exc:
                last_err = exc
                next_name = order[idx + 1].name if idx + 1 < len(order) else "none"
                logger.warning(
                    "[provider_failure] Provider '%s' failed: %s. Trying '%s'.",
                    provider.name, str(exc)[:120], next_name,
                )
                if idx + 1 < len(order):
                    logger.info("[provider_fallback] Falling back to: %s", next_name)

        # All providers exhausted — safe recovery action
        logger.error("[ai_manager] All providers failed in decide_action: %s", last_err)
        return {
            "action": "wait",
            "element_id": None,
            "text": None,
            "key": None,
            "scroll_y": None,
            "rationale": f"All AI providers temporarily degraded. Recovering... ({str(last_err)[:80]})",
            "expected_result": "Re-observe target interface",
            "confidence": 0.3,
            "goal_complete": False,
            "stuck": True,
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
                    logger.info("[ai_manager] Audit report from %s: %d findings", provider.name, len(result.get("findings", [])))
                    return result, provider.name
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "[provider_failure] Provider '%s' failed in audit_run: %s.",
                    provider.name, str(exc)[:120]
                )

        # Fallback: return heuristic findings only
        logger.warning("[ai_manager] All providers failed audit_run — returning heuristic-only summary")
        return {
            "summary": f"Audit completed for goal '{goal}'. {len(heuristic_findings)} issues found via automated heuristics. AI enrichment unavailable.",
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
        """Execute live ping diagnostics on all providers."""
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
