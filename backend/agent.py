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
from .models import Event, Issue, RunCreate, RunState
from .report import generate_report

EventCallback = Callable[[Event], Awaitable[None]]
logger = logging.getLogger("traceqa.agent")


class AutonomousTester:
    def __init__(self, run: RunState, request: RunCreate, emit: EventCallback):
        self.run = run
        self.request = request
        self.emit = emit
        self.trajectory: list[dict[str, Any]] = []
        self.screenshots: list[bytes] = []
        self.journey_nodes: list[dict[str, Any]] = []
        self.journey_edges: list[dict[str, Any]] = []

    async def _log(self, kind: str, message: str, **data: Any) -> None:
        await self.emit(Event(kind=kind, message=message, data=data))

    async def execute(self) -> None:
        run_dir = RUNS_DIR / self.run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        browser = BrowserRunner(run_dir)
        self.run.status = "running"
        self.run.started_at = datetime.now(timezone.utc).isoformat()
        logger.info("[run_created] run_id=%s goal=%r target=%s", self.run.id, self.request.goal, self.request.target_url)
        await self._log(
            "run",
            f"Autonomous test run started for goal: {self.request.goal}",
            goal=self.request.goal,
            target=self.request.target_url,
            provider_mode=ai_provider_manager.mode,
        )

        try:
            total_passes = self.request.exploration_passes
            successful_paths = 0

            for pass_no in range(1, total_passes + 1):
                logger.info("[browser_launch] pass=%d target=%s", pass_no, self.request.target_url)
                await browser.start(self.request.target_url)
                logger.info("[target_navigation] pass=%d url=%s", pass_no, self.request.target_url)
                await self._log("pass", f"Exploration pass {pass_no} of {total_passes} started", pass_no=pass_no)
                pass_history: list[dict[str, Any]] = []
                seen_signatures: set[str] = set()
                prev_node_id: str | None = None

                for step in range(1, self.request.max_steps + 1):
                    # 1. Observe
                    obs = await browser.observe(step)
                    self.screenshots.append(obs.screenshot)
                    self.run.latest_screenshot = f"/artifacts/{self.run.id}/step_{step:03d}.png"
                    logger.info("[observation_created] step=%d url=%s elements=%d", step, obs.url, len(obs.ui_map))

                    is_loop = obs.state_signature in seen_signatures
                    seen_signatures.add(obs.state_signature)

                    # Create Journey Node
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

                    # 2. Decide Path Hint
                    path_hint = ""
                    if pass_no > 1:
                        prev_actions = [h.get("action") + ":" + str(h.get("element_id")) for h in self.trajectory if h.get("pass_no") == 1]
                        path_hint = (
                            f"EXPLORATION PASS {pass_no}: Find an ALTERNATIVE path to complete the goal. "
                            f"For example, if Pass 1 used filters, use the search input; if Pass 1 used search, use category filters. "
                            f"Avoid repeating Pass 1 sequence: {', '.join(prev_actions[:6])}"
                        )

                    # 3. Model Decision via Central AI Provider Manager
                    # Run in thread to avoid blocking async event loop during inference
                    logger.info("[provider_selected] step=%d mode=%s", step, ai_provider_manager.mode)
                    raw_decision, acting_provider = await asyncio.to_thread(
                        ai_provider_manager.decide_action,
                        self.request.goal,
                        obs.screenshot,
                        obs.ui_map,
                        obs.ax_tree,
                        pass_history,
                        path_hint,
                    )

                    # 4. Action Validation & Safety Pipeline
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
                    logger.info("[action_generated] step=%d provider=%s action=%s element=%s valid=%s", step, acting_provider, action, decision.get('element_id'), is_valid)

                    await self._log(
                        "agent",
                        f"Step {step} [{acting_provider.upper()}]: Chose {action}",
                        **decision,
                    )

                    # 5. Execute Action
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
                                # Automatic recovery from modal occlusion
                                await self._log("recovery", "Element occluded by modal; attempting to dismiss overlay")
                                dis = await browser.dismiss_modal()
                                if dis.get("dismissed"):
                                    await self._log("recovery", "Modal dismissed; retrying target click")
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

                    elif action == "scroll":
                        await browser.scroll(int(decision.get("scroll_y") or 450))

                    elif action == "press_key":
                        await browser.press_key(decision.get("key") or "Enter")

                    elif action == "dismiss_modal":
                        action_result = await browser.dismiss_modal()

                    elif action == "back":
                        await browser.back()

                    elif action == "wait":
                        await asyncio.sleep(0.5)

                    elif action == "finish":
                        pass

                    # 6. Annotate Step Screenshot for Visual Tracing
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

                    # Goal Completion Check
                    if action == "finish" or decision.get("goal_complete"):
                        successful_paths += 1
                        self.run.goal_completed = True
                        for n in self.journey_nodes:
                            if n["id"] == node_id:
                                n["is_goal"] = True
                        await self._log(
                            "success",
                            f"Goal completed in pass {pass_no} at step {step} using {acting_provider.upper()}!",
                            pass_no=pass_no,
                            step=step,
                            provider=acting_provider,
                        )
                        break

                    # Recovery if agent flags stuck state
                    if decision.get("stuck") and action != "back":
                        await browser.back()
                        await self._log("recovery", "Agent flagged stuck state; executed backward navigation")

                await browser.close()
                if pass_no < total_passes:
                    self.run.paths_discovered = successful_paths

            self.run.paths_discovered = max(successful_paths, 1 if self.run.goal_completed else 0)

            # 7. Auditing Phase (WCAG Accessibility & Layout Verification)
            await self._log("audit", "Executing deep accessibility and layout heuristics audit")
            logger.info("[evidence_captured] run_id=%s steps=%d", self.run.id, self.run.step_count)
            acc_data = await self._audit_with_fresh_browser(run_dir)
            acc_list = acc_data.get("accessibility", [])
            layout_list = acc_data.get("layout", [])

            heuristic_issues = build_heuristic_findings(self.trajectory, acc_list, layout_list)
            audit_seed = [x.model_dump() for x in heuristic_issues]

            # Multimodal Audit Review via Central Provider Manager (non-blocking)
            ai_audit, audit_provider = await asyncio.to_thread(
                ai_provider_manager.audit_run,
                self.request.goal, self.screenshots[-4:], self.trajectory, audit_seed
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

            # 8. Compute Metrics & Friction Index
            self.run.metrics = calculate_metrics(
                self.trajectory,
                self.run.issues,
                self.run.goal_completed,
                self.run.paths_discovered,
            )
            self.run.metrics["audit_provider"] = audit_provider

            # 9. Generate Standalone HTML & JSON Report
            summary_text = (
                ai_audit.get("summary")
                or f"Audit completed for goal '{self.request.goal}' with {len(self.run.issues)} findings."
            )
            generate_report(
                run_dir=run_dir,
                run_id=self.run.id,
                goal=self.request.goal,
                target_url=self.request.target_url,
                status="completed" if self.run.goal_completed else "partial",
                trajectory=self.trajectory,
                issues=self.run.issues,
                summary=summary_text,
                metrics=self.run.metrics,
                journey=self.run.journey,
            )

            self.run.report_url = f"/artifacts/{self.run.id}/report.html"
            self.run.status = "completed"
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.info("[run_completed] run_id=%s issues=%d friction=%s wcag=%s", self.run.id, len(self.run.issues), self.run.metrics.get('friction_score'), self.run.metrics.get('wcag_grade'))
            await self._log(
                "done",
                f"Audit finished: {len(self.run.issues)} issues detected. Friction Score: {self.run.metrics.get('friction_score')}/100. WCAG Grade: {self.run.metrics.get('wcag_grade')}.",
                issues_count=len(self.run.issues),
                report_url=self.run.report_url,
                provider=audit_provider,
            )

        except Exception as exc:
            self.run.status = "failed"
            self.run.error = str(exc)
            self.run.finished_at = datetime.now(timezone.utc).isoformat()
            logger.error("[run_failed] run_id=%s error=%s", self.run.id, str(exc)[:200])
            await self._log("error", f"Run failed: {str(exc)}")
            try:
                await browser.close()
            except Exception:
                pass

    async def _audit_with_fresh_browser(self, run_dir: Path) -> dict[str, Any]:
        fresh = BrowserRunner(run_dir / "audit")
        (run_dir / "audit").mkdir(exist_ok=True)
        try:
            await fresh.start(self.request.target_url)
            acc = await fresh.audit_accessibility()
            layout = await fresh.audit_layout()
            return {"accessibility": acc, "layout": layout}
        finally:
            await fresh.close()

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
