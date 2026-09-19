from __future__ import annotations

import asyncio
import logging
import os
import time
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

    async def decide_ensemble_action_async(
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
        True Concurrent Multi-Model Consensus Architecture:
        1. Run all configured providers (Groq, Gemini, Local Gemma) in parallel via asyncio.gather.
        2. Total inference latency approaches the slowest provider, NOT their sum.
        3. Isolated failure handling: failure of one provider never crashes the run.
        4. Fast local deterministic consensus (< 2ms): resolves proposals without an extra LLM round.
        5. Conservative finish evidence guard.
        """
        import asyncio
        import time

        proposals: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        latencies: dict[str, float] = {}

        async def _invoke_provider(prov: Any) -> dict[str, Any]:
            if hasattr(prov, "decide_action_async"):
                res = prov.decide_action_async(
                    goal=goal, screenshot=screenshot, ui_map=ui_map,
                    ax_tree=ax_tree, history=history, path_hint=path_hint,
                )
                if asyncio.iscoroutine(res):
                    return await res
                if isinstance(res, dict):
                    return res
            if hasattr(prov, "decide_action"):
                res = await asyncio.to_thread(
                    prov.decide_action,
                    goal=goal, screenshot=screenshot, ui_map=ui_map,
                    ax_tree=ax_tree, history=history, path_hint=path_hint,
                )
                if isinstance(res, tuple):
                    return res[0]
                return res
            raise RuntimeError("Provider does not support decide_action")

        # 1. Determine active providers based on mode & health
        coros: list[tuple[str, Any]] = []

        if self.mode in ("LOCAL", "OFFLINE"):
            coros.append(("local_gemma", _invoke_provider(self.local_gemma)))
        else:
            # Parallel cloud providers
            if self.groq.is_configured() and getattr(self.groq.health, "status", "healthy") != "unhealthy":
                coros.append(("groq", _invoke_provider(self.groq)))

            if self.gemini.is_configured() and getattr(self.gemini.health, "status", "healthy") != "unhealthy":
                coros.append(("gemini", _invoke_provider(self.gemini)))

        # 2. Concurrently execute all model requests
        inference_start = time.perf_counter()
        if coros:
            raw_results = await asyncio.gather(*(c[1] for c in coros), return_exceptions=True)
            for (p_name, _), res in zip(coros, raw_results):
                if isinstance(res, Exception):
                    errors[p_name] = str(res)[:120]
                    logger.warning("[provider_parallel_failure] Provider '%s' failed: %s", p_name, res)
                elif isinstance(res, dict) and res.get("action"):
                    proposals.append({
                        "provider": p_name,
                        "model": self.providers[p_name].model,
                        "action": res.get("action"),
                        "element_id": res.get("element_id"),
                        "text": res.get("text"),
                        "key": res.get("key"),
                        "scroll_y": res.get("scroll_y"),
                        "confidence": float(res.get("confidence", 0.6)),
                        "rationale": res.get("rationale", ""),
                        "expected_result": res.get("expected_result", ""),
                        "goal_complete": res.get("goal_complete", False),
                        "stuck": res.get("stuck", False),
                        "raw_result": res,
                    })
                    latencies[p_name] = round((time.perf_counter() - inference_start) * 1000.0, 1)

        total_inference_ms = round((time.perf_counter() - inference_start) * 1000.0, 1)

        # 3. Fast Deterministic Local Consensus (< 2ms)
        consensus_start = time.perf_counter()
        agreed = True
        disagreement_info: dict[str, Any] | None = None
        selected_provider = "fallback"
        selected_action: dict[str, Any]

        valid_eids = {e.get("id") for e in ui_map if isinstance(e, dict) and e.get("id")}

        if len(proposals) >= 2:
            p1, p2 = proposals[0], proposals[1]
            same_action = p1.get("action") == p2.get("action")
            same_element = p1.get("element_id") == p2.get("element_id")

            if same_action and (same_element or not p1.get("element_id")):
                # Strong multi-model agreement!
                agreed = True
                selected_provider = "consensus"
                selected_action = dict(p1.get("raw_result") or p1)
                selected_action["confidence"] = min(1.0, float(selected_action.get("confidence", 0.6)) + 0.2)
                selected_action["rationale"] = (
                    f"[Multi-Model Consensus] {p1['provider']} and {p2['provider']} agreed on {selected_action.get('action')}. "
                    + f"{selected_action.get('rationale', '')}"
                )
            else:
                # Disagreement resolved locally via deterministic scoring
                agreed = False
                disagreement_info = {
                    "step": step,
                    f"{p1['provider']}_action": f"{p1.get('action')}:{p1.get('element_id')}",
                    f"{p2['provider']}_action": f"{p2.get('action')}:{p2.get('element_id')}",
                    "reason": "Models selected different interaction targets",
                }

                # If one proposed finish but second model did not, reject premature finish
                if p1.get("action") == "finish" and p2.get("action") != "finish":
                    selected_provider = p2["provider"]
                    selected_action = dict(p2.get("raw_result") or p2)
                    selected_action["goal_complete"] = False
                elif p2.get("action") == "finish" and p1.get("action") != "finish":
                    selected_provider = p1["provider"]
                    selected_action = dict(p1.get("raw_result") or p1)
                    selected_action["goal_complete"] = False
                else:
                    # Score proposals: element validity, confidence, avoidance of loops
                    def score_prop(p: dict[str, Any]) -> float:
                        score = float(p.get("confidence", 0.5))
                        eid = p.get("element_id")
                        if eid and eid in valid_eids:
                            score += 0.35
                        elif eid and eid not in valid_eids:
                            score -= 0.5
                        # Penalize repeating failed element from history
                        recent_eids = [h.get("element_id") for h in history[-2:] if h.get("element_id")]
                        if eid and eid in recent_eids:
                            score -= 0.25
                        return score

                    s1 = score_prop(p1)
                    s2 = score_prop(p2)
                    best = p2 if s2 > s1 else p1
                    selected_provider = best["provider"]
                    selected_action = dict(best.get("raw_result") or best)

        elif len(proposals) == 1:
            selected_provider = proposals[0]["provider"]
            selected_action = dict(proposals[0].get("raw_result") or proposals[0])
        else:
            # All active cloud providers failed / unavailable — Local Gemma fallback or safe wait
            logger.warning("[ai_manager] Cloud providers failed, falling back to Local Gemma")
            try:
                gemma_res = await self.local_gemma.decide_action_async(
                    goal=goal, screenshot=screenshot, ui_map=ui_map,
                    ax_tree=ax_tree, history=history, path_hint=path_hint,
                )
                selected_action = gemma_res
                selected_provider = "local_gemma"
            except Exception as e:
                selected_action = {
                    "action": "wait",
                    "element_id": None, "text": None, "key": None, "scroll_y": None,
                    "rationale": f"All AI providers temporarily unavailable. Re-observing... ({e})",
                    "expected_result": "Re-observe",
                    "confidence": 0.3, "goal_complete": False, "stuck": True,
                }
                selected_provider = "fallback"

        # 4. Enforce Conservative Completion Guard
        if selected_action.get("action") == "finish" or selected_action.get("goal_complete"):
            if can_finish_veto and not can_finish_veto[0]:
                logger.warning("[consensus_finish_vetoed] %s", can_finish_veto[1])
                selected_action["action"] = "scroll"
                selected_action["scroll_y"] = 400
                selected_action["goal_complete"] = False
                selected_action["rationale"] = f"Premature finish vetoed: {can_finish_veto[1]}"

        consensus_duration_ms = round((time.perf_counter() - consensus_start) * 1000.0, 2)

        telemetry = {
            "step": step,
            "agreed": agreed,
            "selected_provider": selected_provider,
            "proposals": [
                {k: v for k, v in p.items() if k != "raw_result"} for p in proposals
            ],
            "disagreement": disagreement_info,
            "confidence": selected_action.get("confidence", 0.5),
            "rationale": selected_action.get("rationale", ""),
            "inference_duration_ms": total_inference_ms,
            "consensus_duration_ms": consensus_duration_ms,
            "provider_latencies": latencies,
            "errors": errors,
        }
        return selected_action, telemetry

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
        """Synchronous wrapper for decide_ensemble_action_async."""
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(
                    asyncio.run,
                    self.decide_ensemble_action_async(
                        goal, current_url, screenshot, ui_map, ax_tree,
                        history, path_hint, step, can_finish_veto,
                    ),
                ).result()
        else:
            return asyncio.run(
                self.decide_ensemble_action_async(
                    goal, current_url, screenshot, ui_map, ax_tree,
                    history, path_hint, step, can_finish_veto,
                )
            )

    async def audit_run_ensemble_async(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """
        Concurrent Multi-Model Audit:
        Runs Gemini (visual/a11y) and Groq (UX/navigation) in parallel via asyncio.gather.
        """
        import asyncio

        async def _invoke_audit(prov: Any) -> dict[str, Any]:
            if hasattr(prov, "audit_run_async"):
                res = prov.audit_run_async(goal, screenshots, trajectory, heuristic_findings)
                if asyncio.iscoroutine(res):
                    return await res
                if isinstance(res, dict):
                    return res
            if hasattr(prov, "audit_run"):
                res = await asyncio.to_thread(
                    prov.audit_run, goal, screenshots, trajectory, heuristic_findings
                )
                if isinstance(res, tuple):
                    return res[0]
                return res
            raise RuntimeError("Provider does not support audit_run")

        tasks = []
        task_names = []

        if self.gemini.is_configured() and getattr(self.gemini.health, "status", "healthy") != "unhealthy":
            tasks.append(_invoke_audit(self.gemini))
            task_names.append("gemini")

        if self.groq.is_configured() and getattr(self.groq.health, "status", "healthy") != "unhealthy":
            tasks.append(_invoke_audit(self.groq))
            task_names.append("groq")

        all_findings: list[dict[str, Any]] = []
        providers_used: list[str] = []
        summary = ""
        friction = "Low"

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, res in zip(task_names, results):
                if isinstance(res, Exception):
                    logger.warning("[audit_parallel_failure] %s: %s", name, res)
                elif isinstance(res, dict):
                    providers_used.append(name)
                    findings = res.get("findings", [])
                    all_findings.extend(findings)
                    if not summary and res.get("summary"):
                        summary = res["summary"]
                    if res.get("friction_rating") in ("High", "Critical"):
                        friction = res["friction_rating"]

        # Deduplicate findings by title
        seen_titles = set()
        unique_findings = []
        for f in all_findings:
            title = f.get("title", "").strip().lower()
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_findings.append(f)

        provider_label = "+".join(providers_used) if providers_used else "ensemble"
        return {
            "summary": summary or f"Ensemble audit completed with {len(unique_findings)} findings across models.",
            "friction_rating": friction,
            "findings": unique_findings[:15],
            "providers_used": providers_used,
        }, provider_label

    def audit_run_ensemble(
        self,
        goal: str,
        screenshots: list[bytes],
        trajectory: list[dict[str, Any]],
        heuristic_findings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """Synchronous wrapper for audit_run_ensemble_async."""
        import concurrent.futures
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(
                    asyncio.run,
                    self.audit_run_ensemble_async(goal, screenshots, trajectory, heuristic_findings),
                ).result()
        else:
            return asyncio.run(
                self.audit_run_ensemble_async(goal, screenshots, trajectory, heuristic_findings)
            )


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
