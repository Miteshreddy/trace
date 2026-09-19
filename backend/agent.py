"""
AutonomousTester — TRACE//QA agent loop.

Key improvements:
- URL normalization via url_utils before any navigation.
- Blank-page gate: if BrowserRunner.start() returns navigation_state != "usable",
  the agent loop is skipped and the run reports an honest failure.
- Goal verification: AI saying goal_complete is NOT trusted unconditionally.
  _verify_goal_completion() checks observable evidence before marking success.
- Structured lifecycle events (RUN_CREATED, NAVIGATION_ATTEMPT, etc.).
- Audit guard: _audit_with_fresh_browser() only runs if the target is reachable.
- try/finally for guaranteed browser cleanup on every path.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .action_validator import ActionValidator
from .ai_providers import ai_provider_manager
from .auditor import build_heuristic_findings, calculate_metrics
from .browser import BrowserRunner
from .config import RUNS_DIR
from .goal_verifier import GoalVerificationEngine
from .models import Event, Issue, NavigationDiagnostics, RunCreate, RunState
from .report import generate_report
from .url_utils import classify_navigation_error, normalize_url

EventCallback = Callable[[Event], Awaitable[None]]
logger = logging.getLogger("traceqa.agent")

_GV_MIN_BODY_LENGTH = 150
_GV_MIN_ELEMENTS = 2
_GV_KEYWORD_THRESHOLD = 0.2


class AutonomousTester:
    def __init__(self, run: RunState, request: RunCreate, emit: EventCallback):
        self.run = run
        self.request = request
        self.emit = emit
        self.trajectory: list[dict[str, Any]] = []
        self.screenshots: list[bytes] = []
        self.journey_nodes: list[dict[str, Any]] = []
        self.journey_edges: list[dict[str, Any]] = []
        # Decompose natural language goal into verifiable milestones
        self.goal_plan = GoalVerificationEngine.decompose_goal(self.request.goal)
        self.run.subgoals = [s.model_dump() for s in self.goal_plan.subgoals]

    async def _log(self, kind: str, message: str, **data: Any) -> None:
        await self.emit(Event(kind=kind, message=message, data=data))

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def execute(self) -> None:
        run_dir = RUNS_DIR / self.run.id
        run_dir.mkdir(parents=True, exist_ok=True)

        # ── Phase 2: URL Normalization ────────────────────────────────
        try:
            original_url, normalized_url = normalize_url(self.request.target_url)
        except ValueError as exc:
            # Invalid URL — fail immediately with a clear message
            self.run.status = "failed"
            self.run.error = str(exc)
            self.run.original_target_url = self.request.target_url
            self.run.normalized_target_url = self.request.target_url
            self.run.navigation_state = "error"
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.error("[url_normalization_failed] %s", exc)
            await self._log("error", f"Invalid target URL: {exc}")
            return

        self.run.original_target_url = original_url
        self.run.normalized_target_url = normalized_url
        self.run.current_url = normalized_url
        self.run.last_observed_url = normalized_url
        # Use normalized URL for all browser navigation
        effective_url = normalized_url

        self.run.status = "running"
        self.run.started_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[run_created] run_id=%s goal=%r original_url=%s normalized_url=%s",
            self.run.id, self.request.goal, original_url, normalized_url,
        )
        await self._log(
            "run",
            f"Autonomous test run started for goal: {self.request.goal}",
            goal=self.request.goal,
            original_target=original_url,
            target=effective_url,
            provider_mode=ai_provider_manager.mode,
            subgoals=self.run.subgoals,
        )
        await self._log(
            "goal_planned",
            f"Goal decomposed into {len(self.goal_plan.subgoals)} milestones",
            subgoals=self.run.subgoals,
            success_conditions=self.goal_plan.success_conditions,
        )

        browser = BrowserRunner(run_dir)

        try:
            total_passes = self.request.exploration_passes
            successful_paths = 0

            for pass_no in range(1, total_passes + 1):
                logger.info("[browser_launch] pass=%d target=%s", pass_no, effective_url)
                await self._log(
                    "nav",
                    f"Launching browser — navigating to {effective_url}",
                    pass_no=pass_no,
                    url=effective_url,
                )

                # ── Phase 4/5: Robust navigation ─────────────────────
                nav_diag = await browser.start(effective_url)
                self.run.navigation_diagnostics = nav_diag
                self.run.final_url = nav_diag.final_url
                self.run.current_url = nav_diag.final_url or effective_url
                self.run.last_observed_url = nav_diag.final_url or effective_url
                self.run.navigation_state = nav_diag.navigation_state
                self.run.redirect_chain = browser.get_redirect_chain()

                logger.info(
                    "[navigation_result] pass=%d state=%s final_url=%s body_len=%d elements=%d",
                    pass_no, nav_diag.navigation_state, nav_diag.final_url,
                    nav_diag.body_text_length, nav_diag.interactive_elements,
                )

                if nav_diag.navigation_state == "usable":
                    await self._log(
                        "nav_success",
                        f"Target loaded — URL: {nav_diag.final_url} | Title: {nav_diag.title}",
                        navigation_state=nav_diag.navigation_state,
                        final_url=nav_diag.final_url,
                        title=nav_diag.title,
                        body_text_length=nav_diag.body_text_length,
                        interactive_elements=nav_diag.interactive_elements,
                    )
                else:
                    # ── Phase 6: Blank-page gate ───────────────────────
                    error_detail = ""
                    if nav_diag.error_message:
                        error_detail = f" — {nav_diag.error_message}"
                    if nav_diag.navigation_state == "blocked":
                        msg = (
                            f"Target appears to be blocking automation or presenting "
                            f"an interstitial page{error_detail}. "
                            f"Final URL: {nav_diag.final_url}. "
                            f"Body length: {nav_diag.body_text_length}."
                        )
                    elif nav_diag.navigation_state == "blank":
                        msg = (
                            f"Target page is blank or failed to render content{error_detail}. "
                            f"Final URL: {nav_diag.final_url}. "
                            f"Body length: {nav_diag.body_text_length} chars, "
                            f"{nav_diag.interactive_elements} interactive elements."
                        )
                    else:
                        msg = (
                            f"Target navigation resulted in state '{nav_diag.navigation_state}'{error_detail}. "
                            f"Final URL: {nav_diag.final_url}."
                        )

                    logger.warning("[navigation_gate_fail] pass=%d %s", pass_no, msg)
                    await self._log(
                        "nav_failure",
                        msg,
                        navigation_state=nav_diag.navigation_state,
                        final_url=nav_diag.final_url,
                        error_type=nav_diag.error_type,
                        error_message=nav_diag.error_message,
                    )

                    # Still proceed to generate a partial report — don't crash silently
                    self.run.status = "failed"
                    self.run.error = msg
                    self.run.finished_at = datetime.now(timezone.utc).isoformat()
                    await browser.close()
                    # Generate minimal report so the user sees what happened
                    self._generate_failure_report(run_dir, msg, nav_diag)
                    return

                await self._log("pass", f"Exploration pass {pass_no} of {total_passes} started", pass_no=pass_no)
                pass_history: list[dict[str, Any]] = []
                seen_signatures: set[str] = set()
                prev_node_id: str | None = None
                consecutive_blank = 0

                for step in range(1, self.request.max_steps + 1):
                    # ── 1. Observe ─────────────────────────────────────
                    obs = await browser.observe(step)
                    self.screenshots.append(obs.screenshot)
                    self.run.latest_screenshot = f"/artifacts/{self.run.id}/step_{step:03d}.png"

                    # Real-time URL synchronization
                    current_url = browser.get_current_url() or obs.url
                    if current_url and current_url != self.run.current_url:
                        prev_url = self.run.current_url
                        self.run.current_url = current_url
                        self.run.last_observed_url = current_url
                        self.run.redirect_chain = browser.get_redirect_chain()
                        logger.info("[url_changed] step=%d %s ➔ %s", step, prev_url, current_url)
                        await self._log(
                            "url_changed",
                            f"Target navigated: {prev_url} ➔ {current_url}",
                            previous_url=prev_url,
                            current_url=current_url,
                            step=step,
                        )

                    logger.info(
                        "[observation_created] step=%d url=%s elements=%d body_len=%d nav_state=%s",
                        step, obs.url, len(obs.ui_map), obs.body_text_length, obs.navigation_state,
                    )

                    # Update goal milestone progress
                    self.goal_plan, gv_eval = GoalVerificationEngine.verify_observation(
                        self.goal_plan, obs, browser.page, pass_history[-1] if pass_history else None
                    )
                    self.run.subgoals = [s.model_dump() for s in self.goal_plan.subgoals]
                    for sig in gv_eval.get("signals", []):
                        await self._log("subgoal_completed", f"Milestone signal verified: {sig}", step=step, evaluation=gv_eval)

                    # Track consecutive blank observations (loop protection)
                    if obs.navigation_state == "blank":
                        consecutive_blank += 1
                    else:
                        consecutive_blank = 0

                    if consecutive_blank >= 3:
                        await self._log(
                            "recovery",
                            f"Page blank for {consecutive_blank} consecutive steps — stopping pass",
                            step=step,
                        )
                        break

                    is_loop = obs.state_signature in seen_signatures
                    seen_signatures.add(obs.state_signature)

                    # Journey graph
                    node_id = f"s_{obs.state_signature}"
                    node_label = f"Step {step}: {obs.title or 'View'}"
                    if not any(n["id"] == node_id for n in self.journey_nodes):
                        self.journey_nodes.append({
                            "id": node_id,
                            "label": node_label,
                            "url": obs.url,
                            "step": step,
                            "screenshot": self.run.latest_screenshot,
                            "is_goal": False,
                            "is_loop": is_loop,
                        })

                    if prev_node_id and prev_node_id != node_id:
                        edge_label = pass_history[-1].get("action", "nav") if pass_history else "start"
                        self.journey_edges.append({
                            "source": prev_node_id,
                            "target": node_id,
                            "action": edge_label,
                            "label": edge_label,
                            "pass_no": pass_no,
                        })
                    prev_node_id = node_id

                    # ── 2. Path hint for multi-pass exploration ─────────
                    path_hint = ""
                    if pass_no > 1:
                        prev_actions = [
                            h.get("action") + ":" + str(h.get("element_id"))
                            for h in self.trajectory if h.get("pass_no") == 1
                        ]
                        path_hint = (
                            f"EXPLORATION PASS {pass_no}: Find an ALTERNATIVE path to complete the goal. "
                            f"Avoid repeating Pass 1 sequence: {', '.join(prev_actions[:6])}"
                        )

                    # ── 3. Multi-Model Consensus Decision ──────────────
                    can_finish_veto = GoalVerificationEngine.can_finish(self.goal_plan, obs)
                    raw_decision, consensus_meta = await asyncio.to_thread(
                        ai_provider_manager.decide_ensemble_action,
                        self.request.goal,
                        obs.url,
                        obs.screenshot,
                        obs.ui_map,
                        obs.ax_tree,
                        pass_history,
                        path_hint,
                        step,
                        can_finish_veto,
                    )
                    self.run.consensus_history.append(consensus_meta)
                    acting_provider = consensus_meta.get("selected_provider", "groq")

                    if not consensus_meta.get("agreed") and consensus_meta.get("disagreement"):
                        await self._log(
                            "model_disagreement",
                            f"Model disagreement at step {step}: Critic chose {acting_provider}",
                            **consensus_meta["disagreement"],
                        )

                    await self._log(
                        "consensus",
                        f"Step {step} Decision [{acting_provider.upper()}]: {raw_decision.get('action')}"
                        + (f" on {raw_decision.get('element_id')}" if raw_decision.get('element_id') else ""),
                        **consensus_meta,
                    )

                    # ── 4. ActionValidator & Safety Pipeline ─────────────
                    is_valid, decision, rejection_reason = ActionValidator.validate(raw_decision, obs.ui_map)
                    if not is_valid:
                        logger.warning("[action_validated] REJECTED step=%d reason=%s", step, rejection_reason)
                        await self._log(
                            "safety",
                            f"Action safety guard: {rejection_reason}",
                            provider=acting_provider,
                            raw=raw_decision,
                        )

                    action = decision.get("action", "wait")
                    rationale = decision.get("rationale", "")
                    decision["step"] = step
                    decision["provider"] = acting_provider
                    logger.info(
                        "[action_generated] step=%d provider=%s action=%s element=%s valid=%s",
                        step, acting_provider, action, decision.get("element_id"), is_valid,
                    )

                    await self._log(
                        "agent",
                        f"Step {step} [{acting_provider.upper()}]: Chose {action}",
                        **decision,
                    )

                    # ── 5. Execute Action ────────────────────────────────
                    action_result: dict[str, Any] = {"ok": True}
                    click_pt: tuple[float, float] | None = None
                    target_eid = decision.get("element_id")

                    if action == "click":
                        if not target_eid:
                            action_result = {"ok": False, "error": "No element_id selected"}
                        else:
                            action_result = await browser.click(target_eid)
                            if action_result.get("ok"):
                                click_pt = (action_result.get("x", 0), action_result.get("y", 0))
                            elif action_result.get("occluded"):
                                await self._log("recovery", "Element occluded; attempting modal dismiss")
                                dis = await browser.dismiss_modal()
                                if dis.get("dismissed"):
                                    await self._log("recovery", "Modal dismissed; retrying click")
                                    action_result = await browser.click(target_eid)
                                    if action_result.get("ok"):
                                        click_pt = (action_result.get("x", 0), action_result.get("y", 0))

                    elif action == "type":
                        text_to_type = decision.get("text") or ""
                        if not target_eid:
                            action_result = {"ok": False, "error": "No input selected"}
                        else:
                            action_result = await browser.type_into(target_eid, text_to_type)
                            if action_result.get("ok"):
                                click_pt = (action_result.get("x", 0), action_result.get("y", 0))
                            if "enter" in decision.get("expected_result", "").lower():
                                await browser.press_key("Enter")
                                await self._log("settle", "Allowing dynamic search results to render and settle")
                                await browser.settle_dynamic_content()

                    elif action == "scroll":
                        action_result = await browser.scroll(int(decision.get("scroll_y") or 450))

                    elif action == "press_key":
                        action_result = await browser.press_key(decision.get("key") or "Enter")
                        if decision.get("key", "").lower() == "enter":
                            await self._log("settle", "Allowing dynamic search results to render and settle")
                            await browser.settle_dynamic_content()

                    elif action == "dismiss_modal":
                        action_result = await browser.dismiss_modal()

                    elif action == "back":
                        await browser.back()

                    elif action == "wait":
                        await asyncio.sleep(0.5)

                    elif action == "finish":
                        pass  # handled below in goal check

                    # Re-check URL after action
                    act_url = browser.get_current_url()
                    if act_url and act_url != self.run.current_url:
                        prev_u = self.run.current_url
                        self.run.current_url = act_url
                        self.run.last_observed_url = act_url
                        self.run.redirect_chain = browser.get_redirect_chain()
                        logger.info("[url_changed] post_action step=%d %s -> %s", step, prev_u, act_url)
                        await self._log(
                            "url_changed",
                            f"URL changed after action: {prev_u} -> {act_url}",
                            previous_url=prev_u,
                            current_url=act_url,
                            step=step,
                        )

                    # ── 6. Annotate Screenshot ───────────────────────────
                    browser.annotate_step_screenshot(
                        step=step,
                        raw_screenshot=obs.screenshot,
                        action=action,
                        element_id=target_eid,
                        click_point=click_pt,
                    )

                    record = {
                        "step": step,
                        "pass_no": pass_no,
                        "action": action,
                        "provider": acting_provider,
                        "element_id": target_eid,
                        "text": decision.get("text"),
                        "key": decision.get("key"),
                        "rationale": rationale,
                        "expected_result": decision.get("expected_result", ""),
                        "confidence": decision.get("confidence", 0.5),
                        "goal_complete": decision.get("goal_complete", False),
                        "stuck": decision.get("stuck", False),
                        "state_signature": obs.state_signature,
                        "url": obs.url,
                        "action_result": action_result,
                    }
                    pass_history.append(record)
                    self.trajectory.append(record)
                    self.run.step_count += 1
                    self.run.journey = {"nodes": self.journey_nodes, "edges": self.journey_edges}

                    # ── Phase 12: Independent Goal Verification ──────────
                    if action == "finish" or decision.get("goal_complete"):
                        can_fin, evidence_str = GoalVerificationEngine.can_finish(self.goal_plan, obs)
                        if can_fin:
                            successful_paths += 1
                            self.run.goal_completed = True
                            self.run.goal_verification_evidence = evidence_str
                            for n in self.journey_nodes:
                                if n["id"] == node_id:
                                    n["is_goal"] = True
                            await self._log(
                                "success",
                                f"Goal completed and verified in pass {pass_no} at step {step}!",
                                pass_no=pass_no,
                                step=step,
                                provider=acting_provider,
                                evidence=evidence_str,
                            )
                            logger.info(
                                "[goal_verified] pass=%d step=%d evidence=%s",
                                pass_no, step, evidence_str[:120],
                            )
                            break
                        else:
                            await self._log(
                                "goal_unverified",
                                f"Completion proposed at step {step}, but independent verification rejected it: {evidence_str}. Continuing...",
                                step=step,
                                evidence=evidence_str,
                                provider=acting_provider,
                            )
                            logger.warning(
                                "[goal_verification_rejected] step=%d evidence=%s",
                                step, evidence_str[:120],
                            )
                            record["goal_complete"] = False
                            self.trajectory[-1]["goal_complete"] = False
                            continue

                    # Recovery if agent flags stuck
                    if decision.get("stuck") and action != "back":
                        await browser.back()
                        await self._log("recovery", "Agent flagged stuck state; executed backward navigation")

                # End of step loop
                await browser.close()

                # Re-create browser for next pass if needed
                if pass_no < total_passes:
                    browser = BrowserRunner(run_dir)
                    self.run.paths_discovered = successful_paths

            self.run.paths_discovered = max(successful_paths, 1 if self.run.goal_completed else 0)

            # ── Phase 21: Audit Guard ──────────────────────────────────
            await self._log("audit", "Executing accessibility and layout heuristics audit")
            logger.info("[evidence_captured] run_id=%s steps=%d", self.run.id, self.run.step_count)

            # Only run audit if target was navigable
            if self.run.navigation_state in ("usable",):
                acc_data = await self._audit_with_fresh_browser(run_dir, effective_url)
                audit_available = acc_data.get("available", True)
            else:
                acc_data = {"accessibility": [], "layout": [], "available": False}
                audit_available = False
                await self._log(
                    "audit_skipped",
                    f"Accessibility audit skipped — target navigation state was '{self.run.navigation_state}'",
                )

            acc_list = acc_data.get("accessibility", [])
            layout_list = acc_data.get("layout", [])

            heuristic_issues = build_heuristic_findings(self.trajectory, acc_list, layout_list)
            audit_seed = [x.model_dump() for x in heuristic_issues]

            # Multimodal Audit via Central Provider Manager (Ensemble)
            ai_audit, audit_provider = await asyncio.to_thread(
                ai_provider_manager.audit_run_ensemble,
                self.request.goal, self.screenshots[-4:], self.trajectory, audit_seed,
            )
            ai_issues = [
                Issue(
                    id=f"ai-finding-{idx}",
                    category=f.get("category", "ux"),
                    severity=f.get("severity", "medium"),
                    title=f.get("title", "Detected UX Issue"),
                    description=f.get("description", ""),
                    evidence=f.get("evidence", ""),
                    recommendation=f.get("recommendation", "Review component implementation."),
                    step=f.get("step"),
                    source="agent",
                )
                for idx, f in enumerate(ai_audit.get("findings", []), 1)
            ]

            combined_issues = self._dedupe_issues(heuristic_issues + ai_issues)
            self.run.issues = combined_issues

            # Add navigation issue to findings if target was problematic
            if not audit_available:
                self.run.issues.insert(0, Issue(
                    id="nav-audit-skipped",
                    category="navigation",
                    severity="critical",
                    title="Accessibility audit skipped — target navigation failed",
                    description=(
                        f"The target page did not load usably (state: {self.run.navigation_state}). "
                        "WCAG accessibility data could not be collected from a blank or blocked page."
                    ),
                    evidence=f"Navigation state: {self.run.navigation_state}. Final URL: {self.run.final_url}",
                    recommendation="Fix target reachability before running accessibility audit.",
                    source="heuristic",
                ))

            # ── Metrics & Report ───────────────────────────────────────
            self.run.metrics = calculate_metrics(
                self.trajectory,
                self.run.issues,
                self.run.goal_completed,
                self.run.paths_discovered,
            )
            self.run.metrics["audit_provider"] = audit_provider

            run_status = "completed" if self.run.goal_completed else "partial"
            summary_text = (
                ai_audit.get("summary")
                or f"Audit completed for goal '{self.request.goal}' with {len(self.run.issues)} findings."
            )

            final_destination = browser.get_current_url() or self.run.current_url or effective_url
            self.run.final_url = final_destination

            generate_report(
                run_dir=run_dir,
                run_id=self.run.id,
                goal=self.request.goal,
                target_url=effective_url,
                status=run_status,
                trajectory=self.trajectory,
                issues=self.run.issues,
                summary=summary_text,
                metrics=self.run.metrics,
                journey=self.run.journey,
                original_target_url=self.run.original_target_url,
                normalized_target_url=self.run.normalized_target_url,
                current_url=self.run.current_url,
                final_url=final_destination,
                redirect_chain=self.run.redirect_chain,
                subgoals=self.run.subgoals,
                navigation_state=self.run.navigation_state,
                navigation_diagnostics=self.run.navigation_diagnostics.model_dump() if hasattr(self.run.navigation_diagnostics, "model_dump") else {},
                goal_verification_evidence=self.run.goal_verification_evidence,
            )


            self.run.report_url = f"/artifacts/{self.run.id}/report.html"
            self.run.status = "completed"
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                "[run_completed] run_id=%s issues=%d friction=%s wcag=%s goal_completed=%s",
                self.run.id, len(self.run.issues),
                self.run.metrics.get("friction_score"),
                self.run.metrics.get("wcag_grade"),
                self.run.goal_completed,
            )
            await self._log(
                "done",
                f"Audit finished: {len(self.run.issues)} issues. "
                f"Friction Score: {self.run.metrics.get('friction_score')}/100. "
                f"WCAG Grade: {self.run.metrics.get('wcag_grade')}. "
                f"Goal completed: {self.run.goal_completed}.",
                issues_count=len(self.run.issues),
                report_url=self.run.report_url,
                provider=audit_provider,
                goal_completed=self.run.goal_completed,
                goal_verification_evidence=self.run.goal_verification_evidence,
            )

        except asyncio.CancelledError:
            # Graceful cancellation
            self.run.status = "cancelled"
            self.run.error = "Run cancelled by user."
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.info("[run_cancelled] run_id=%s", self.run.id)
            raise

        except Exception as exc:
            self.run.status = "failed"
            self.run.error = str(exc)
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.error("[run_failed] run_id=%s error=%s", self.run.id, str(exc)[:200])
            await self._log("error", f"Run failed: {str(exc)}")

        finally:
            # Always close browser regardless of how we exit
            try:
                await browser.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Phase 12 — Independent Goal Verification
    # ------------------------------------------------------------------

    async def _verify_goal_completion(
        self, browser: BrowserRunner, obs: Any
    ) -> tuple[bool, str]:
        """
        Verify observable evidence that the goal was actually achieved.

        Returns (verified: bool, evidence: str).
        """
        evidence_parts: list[str] = []
        score = 0

        # 1. Page has content (not blank)
        if obs.body_text_length >= _GV_MIN_BODY_LENGTH:
            score += 1
            evidence_parts.append(f"Page has {obs.body_text_length} chars of content")
        else:
            evidence_parts.append(f"WARNING: Page body only {obs.body_text_length} chars")

        # 2. Interactive elements present
        if len(obs.ui_map) >= _GV_MIN_ELEMENTS:
            score += 1
            evidence_parts.append(f"{len(obs.ui_map)} interactive elements visible")
        else:
            evidence_parts.append(f"WARNING: Only {len(obs.ui_map)} interactive elements")

        # 3. Keyword match between goal and visible page text
        goal_words = set(self.request.goal.lower().split())
        # Filter out common stop words
        stop_words = {"the", "a", "an", "and", "or", "to", "in", "on", "at", "for",
                      "of", "is", "it", "this", "that", "with", "from", "by"}
        meaningful_words = goal_words - stop_words
        if meaningful_words:
            try:
                body_text = await browser.page.locator("body").inner_text(timeout=3000)  # type: ignore
                body_lower = body_text.lower()
                matched = sum(1 for w in meaningful_words if w in body_lower)
                ratio = matched / len(meaningful_words)
                if ratio >= _GV_KEYWORD_THRESHOLD:
                    score += 1
                    evidence_parts.append(
                        f"Page contains {matched}/{len(meaningful_words)} goal keywords (ratio={ratio:.2f})"
                    )
                else:
                    evidence_parts.append(
                        f"WARNING: Only {matched}/{len(meaningful_words)} goal keywords found (ratio={ratio:.2f})"
                    )
            except Exception:
                evidence_parts.append("WARNING: Could not inspect page text for keyword match")

        # 4. URL changed from original target (shows navigation happened)
        final_url = obs.url
        if final_url and final_url not in ("about:blank", self.run.normalized_target_url):
            score += 1
            evidence_parts.append(f"URL navigated to: {final_url}")

        # 5. Navigation state is usable
        if obs.navigation_state == "usable":
            score += 1
            evidence_parts.append("Navigation state: usable")

        # Need at least 3 of 5 evidence signals
        verified = score >= 3
        evidence_str = " | ".join(evidence_parts)

        logger.info(
            "[goal_verification] score=%d/5 verified=%s evidence=%s",
            score, verified, evidence_str[:200],
        )
        return verified, evidence_str

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    async def _audit_with_fresh_browser(self, run_dir: Path, target_url: str) -> dict[str, Any]:
        """
        Launch a fresh browser session for accessibility/layout audit.
        Guards against auditing a blank/error page.
        """
        audit_dir = run_dir / "audit"
        audit_dir.mkdir(exist_ok=True)
        fresh = BrowserRunner(audit_dir)
        try:
            nav_diag = await fresh.start(target_url)
            if nav_diag.navigation_state not in ("usable",):
                logger.warning(
                    "[audit_skipped] target not usable for audit: state=%s", nav_diag.navigation_state
                )
                return {"accessibility": [], "layout": [], "available": False}

            acc = await fresh.audit_accessibility()
            layout = await fresh.audit_layout()
            return {"accessibility": acc, "layout": layout, "available": True}
        except Exception as exc:
            logger.error("[audit_browser_error] %s", exc)
            return {"accessibility": [], "layout": [], "available": False}
        finally:
            await fresh.close()

    # ------------------------------------------------------------------
    # Failure report helper
    # ------------------------------------------------------------------

    def _generate_failure_report(
        self, run_dir: Path, error_msg: str, nav_diag: NavigationDiagnostics
    ) -> None:
        """Generate a minimal report for navigation-failed runs."""
        try:
            self.run.report_url = f"/artifacts/{self.run.id}/report.html"
            generate_report(
                run_dir=run_dir,
                run_id=self.run.id,
                goal=self.request.goal,
                target_url=self.run.normalized_target_url or self.run.target_url,
                status="failed",
                trajectory=[],
                issues=[
                    Issue(
                        id="nav-failure",
                        category="navigation",
                        severity="critical",
                        title="Target navigation failed",
                        description=error_msg,
                        evidence=(
                            f"Navigation state: {nav_diag.navigation_state}. "
                            f"Final URL: {nav_diag.final_url}. "
                            f"Error type: {nav_diag.error_type}. "
                            f"Attempts: {nav_diag.attempts}."
                        ),
                        recommendation="Verify the target URL is reachable and not blocking automation.",
                        source="heuristic",
                    )
                ],
                summary=error_msg,
                metrics={"steps": 0, "paths": 0, "goal_completed": False,
                         "friction_score": 100, "accessibility_score": 0,
                         "wcag_grade": "Unknown", "accessibility_findings": 0,
                         "ux_findings": 0, "navigation_findings": 1, "visual_findings": 0},
                journey={"nodes": [], "edges": []},
                original_target_url=self.run.original_target_url,
                normalized_target_url=self.run.normalized_target_url,
                current_url=nav_diag.final_url or self.run.normalized_target_url,
                final_url=nav_diag.final_url or self.run.normalized_target_url,
                redirect_chain=self.run.redirect_chain,
                subgoals=self.run.subgoals,
                navigation_state=nav_diag.navigation_state,
                navigation_diagnostics=nav_diag.model_dump() if hasattr(nav_diag, "model_dump") else {},
                goal_verification_evidence="Navigation failed prior to goal execution.",
            )

        except Exception as exc:
            logger.error("[failure_report_error] %s", exc)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe_issues(issues: list[Issue]) -> list[Issue]:
        seen: set[tuple[str, str]] = set()
        out: list[Issue] = []
        for i in issues:
            key = (i.category, i.title.lower().strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(i)
        return out[:30]
