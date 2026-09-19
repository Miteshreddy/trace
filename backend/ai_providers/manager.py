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

    def decide_ensemble_action(
        self,
        goal: str,
        current_url: str,
        screenshot: bytes,
        ui_map: list[dict[str, Any]],
        ax_tree: list[dict[str, Any]],
        history: list[dict[str, Any]],
        path_hint: str = "",
        step: int = 1,
        can_finish_veto: tuple[bool, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        True Role-Based Multi-Model Consensus Architecture:
        1. Groq (DOM / Action Planner): Rapid structured inference with compact DOM/Vision.
        2. Gemini (Visual Planner): Multimodal visual spatial reasoning (if configured & healthy).
        3. Compare proposals.
        4. If disagreement or finish proposed: Critic evaluates and enforces evidence guard.
        5. Returns (selected_action, consensus_telemetry).
        """
        proposals: list[dict[str, Any]] = []
        errors: dict[str, str] = {}

        # 1. Ask Groq (Action Planner)
        groq_action: dict[str, Any] | None = None
        if self.groq.is_configured() and self.groq.health.status != "unhealthy":
            try:
                groq_action = self.groq.decide_action(
                    goal=goal,
                    screenshot=screenshot,
                    ui_map=ui_map,
                    ax_tree=ax_tree,
                    history=history,
                    path_hint=path_hint,
                )
                if groq_action and groq_action.get("action"):
                    proposals.append({
                        "provider": "groq",
                        "model": self.groq.model,
                        "role": "action_planner",
                        "action": groq_action.get("action"),
                        "element_id": groq_action.get("element_id"),
                        "text": groq_action.get("text"),
                        "key": groq_action.get("key"),
                        "confidence": groq_action.get("confidence", 0.6),
                        "rationale": groq_action.get("rationale", ""),
                        "goal_complete": groq_action.get("goal_complete", False),
                    })
            except Exception as e:
                errors["groq"] = str(e)[:120]
                logger.warning("[ensemble_groq_error] %s", e)

        # 2. Ask Gemini (Visual Planner) if configured & not degraded
        gemini_action: dict[str, Any] | None = None
        if self.gemini.is_configured() and self.gemini.health.status != "unhealthy":
            try:
                gemini_action = self.gemini.decide_action(
                    goal=goal,
                    screenshot=screenshot,
                    ui_map=ui_map,
                    ax_tree=ax_tree,
                    history=history,
                    path_hint=path_hint,
                )
                if gemini_action and gemini_action.get("action"):
                    proposals.append({
                        "provider": "gemini",
                        "model": self.gemini.model,
                        "role": "visual_planner",
                        "action": gemini_action.get("action"),
                        "element_id": gemini_action.get("element_id"),
                        "text": gemini_action.get("text"),
                        "key": gemini_action.get("key"),
                        "confidence": gemini_action.get("confidence", 0.6),
                        "rationale": gemini_action.get("rationale", ""),
                        "goal_complete": gemini_action.get("goal_complete", False),
                    })
            except Exception as e:
                errors["gemini"] = str(e)[:120]
                logger.warning("[ensemble_gemini_error] %s", e)

        # 3. Form Consensus Decision
        agreed = True
        disagreement_info: dict[str, Any] | None = None
        critic_verdict = "uncontested"
        selected_provider = "groq"
        selected_action: dict[str, Any]

        if len(proposals) >= 2:
            p1, p2 = proposals[0], proposals[1]
            same_action = p1.get("action") == p2.get("action")
            same_element = p1.get("element_id") == p2.get("element_id")

            if same_action and (same_element or not p1.get("element_id")):
                # Strong multi-model agreement
                agreed = True
                selected_provider = "consensus"
                selected_action = dict(groq_action if groq_action else gemini_action)  # type: ignore
                selected_action["confidence"] = min(1.0, float(selected_action.get("confidence", 0.5)) + 0.2)
                selected_action["rationale"] = f"[Multi-Model Consensus] Groq and Gemini agreed on {selected_action.get('action')}. {selected_action.get('rationale', '')}"
            else:
                # Disagreement detected — Invoke Critic
                agreed = False
                critic_res = self.local_gemma.criticize_proposal(
                    goal=goal,
                    current_url=current_url,
                    history=history,
                    proposals=proposals,
                    ui_map=ui_map,
                )
                critic_verdict = critic_res.get("verdict", "undecided")
                disagreement_info = {
                    "step": step,
                    "groq_action": f"{p1.get('action')}:{p1.get('element_id')}",
                    "gemini_action": f"{p2.get('action')}:{p2.get('element_id')}",
                    "critic_verdict": critic_verdict,
                    "reason": critic_res.get("reason", "Models selected different interaction targets"),
                }
                logger.info("[model_disagreement] step=%d: %s", step, disagreement_info)

                # If one proposed finish but critic or second model disagreed, reject finish!
                if p1.get("action") == "finish" and p2.get("action") != "finish":
                    selected_provider = p2.get("provider", "gemini")
                    selected_action = dict(gemini_action)  # type: ignore
                    selected_action["goal_complete"] = False
                elif p2.get("action") == "finish" and p1.get("action") != "finish":
                    selected_provider = p1.get("provider", "groq")
                    selected_action = dict(groq_action)  # type: ignore
                    selected_action["goal_complete"] = False
                else:
                    # Prefer proposal whose target element is confirmed in UI map
                    e1_ok = any(e.get("id") == p1.get("element_id") for e in ui_map) if p1.get("element_id") else True
                    e2_ok = any(e.get("id") == p2.get("element_id") for e in ui_map) if p2.get("element_id") else True
                    if e1_ok and not e2_ok:
                        selected_provider = p1.get("provider", "groq")
                        selected_action = dict(groq_action)  # type: ignore
                    elif e2_ok and not e1_ok:
                        selected_provider = p2.get("provider", "gemini")
                        selected_action = dict(gemini_action)  # type: ignore
                    else:
                        # Choose higher confidence or Groq
                        if float(p2.get("confidence", 0)) > float(p1.get("confidence", 0)):
                            selected_provider = p2.get("provider", "gemini")
                            selected_action = dict(gemini_action)  # type: ignore
                        else:
                            selected_provider = p1.get("provider", "groq")
                            selected_action = dict(groq_action)  # type: ignore
        elif len(proposals) == 1:
            selected_provider = proposals[0]["provider"]
            selected_action = dict(groq_action if groq_action else gemini_action)  # type: ignore
        else:
            # All cloud providers failed — Fallback to local Gemma or safe wait
            logger.warning("[ai_manager] Cloud providers failed, falling back to Local Gemma")
            try:
                selected_action = self.local_gemma.decide_action(
                    goal=goal,
                    screenshot=screenshot,
                    ui_map=ui_map,
                    ax_tree=ax_tree,
                    history=history,
                    path_hint=path_hint,
                )
                selected_provider = "local_gemma"
            except Exception as e:
                selected_action = {
                    "action": "wait",
                    "element_id": None, "text": None, "key": None, "scroll_y": None,
                    "rationale": f"All AI providers unavailable. Re-observing... ({e})",
                    "expected_result": "Re-observe",
                    "confidence": 0.3, "goal_complete": False, "stuck": True,
                }
                selected_provider = "fallback"

        # 4. Enforce Conservative Completion Guard
        # If any action claims finish or goal_complete, check can_finish_veto
        if selected_action.get("action") == "finish" or selected_action.get("goal_complete"):
            if can_finish_veto and not can_finish_veto[0]:
                logger.warning("[consensus_finish_vetoed] %s", can_finish_veto[1])
                selected_action["action"] = "scroll"
                selected_action["scroll_y"] = 400
                selected_action["goal_complete"] = False
                selected_action["rationale"] = f"Premature finish vetoed: {can_finish_veto[1]}"

        telemetry = {
            "step": step,
            "agreed": agreed,
            "selected_provider": selected_provider,
            "proposals": proposals,
            "critic_verdict": critic_verdict,
            "disagreement": disagreement_info,
            "confidence": selected_action.get("confidence", 0.5),
            "rationale": selected_action.get("rationale", ""),
        }
        return selected_action, telemetry

    def audit_run_ensemble(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """
        Multi-model audit combining Gemini (visual/a11y), Groq (UX/navigation),
        and deterministic heuristics.
        """
        all_findings: list[dict[str, Any]] = []
        providers_used: list[str] = []
        summary = ""
        friction = "Low"

        # 1. Gemini visual audit
        if self.gemini.is_configured() and self.gemini.health.status != "unhealthy":
            try:
                res = self.gemini.audit_run(goal, screenshots, trajectory, heuristic_findings)
                if res and "findings" in res:
                    for f in res.get("findings", []):
                        f["detection_source"] = "gemini_visual"
                        all_findings.append(f)
                    providers_used.append("gemini")
                    if not summary:
                        summary = res.get("summary", "")
                    friction = res.get("friction_rating", friction)
            except Exception as e:
                logger.debug("[gemini_audit_error] %s", e)

        # 2. Groq UX/navigation audit
        if self.groq.is_configured() and self.groq.health.status != "unhealthy":
            try:
                res = self.groq.audit_run(goal, screenshots, trajectory, heuristic_findings)
                if res and "findings" in res:
                    for f in res.get("findings", []):
                        f["detection_source"] = "groq_ux"
                        all_findings.append(f)
                    providers_used.append("groq")
                    if not summary:
                        summary = res.get("summary", "")
            except Exception as e:
                logger.debug("[groq_audit_error] %s", e)

        if not summary:
            summary = f"Multi-model audit completed for goal: '{goal}'. {len(all_findings) + len(heuristic_findings)} findings identified."

        provider_label = "+".join(providers_used) if providers_used else "heuristics"
        return {
            "summary": summary,
            "friction_rating": friction,
            "findings": all_findings,
            "providers_used": providers_used,
        }, provider_label


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
