from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent import AutonomousTester
from .ai_providers import ai_provider_manager
from .config import DEMO_URL, ROOT, RUNS_DIR
from .models import Event, RunCreate, RunState
from .url_utils import classify_navigation_error, normalize_url, validate_url_scheme

app = FastAPI(title="TRACE//QA", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS: dict[str, RunState] = {}
TASKS: dict[str, asyncio.Task[Any]] = {}

app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
app.mount("/demo", StaticFiles(directory=ROOT / "demo", html=True), name="demo")
app.mount("/artifacts", StaticFiles(directory=RUNS_DIR), name="artifacts")


class ProviderSelectRequest(BaseModel):
    mode: str


async def emit(run_id: str, event: Event) -> None:
    run = RUNS.get(run_id)
    if not run:
        return
    run.events.append(event)
    if len(run.events) > 200:
        run.events = run.events[-200:]


async def run_task(run_id: str, req: RunCreate) -> None:
    tester = AutonomousTester(RUNS[run_id], req, lambda event: emit(run_id, event))
    await tester.execute()


@app.get("/", response_class=HTMLResponse)
@app.get("/command", response_class=HTMLResponse)
@app.get("/live", response_class=HTMLResponse)
@app.get("/evaluation", response_class=HTMLResponse)
@app.get("/journeys", response_class=HTMLResponse)
@app.get("/findings", response_class=HTMLResponse)
@app.get("/report", response_class=HTMLResponse)
async def serve_frontend() -> str:
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    providers = ai_provider_manager.get_health_status()
    groq_conf = bool(providers["providers"].get("groq", {}).get("configured"))
    gemini_conf = bool(providers["providers"].get("gemini", {}).get("configured"))
    return {
        "ok": True,
        "demo_url": DEMO_URL,
        "mode": providers["mode"],
        "groq_configured": groq_conf,
        "gemini_configured": gemini_conf,
        "model_configured": groq_conf or gemini_conf,
    }


@app.get("/api/providers")
async def get_providers() -> dict[str, Any]:
    """Return health status and telemetry for all AI providers without exposing keys."""
    return ai_provider_manager.get_health_status()


@app.post("/api/providers/select")
async def select_provider(req: ProviderSelectRequest) -> dict[str, Any]:
    """Change the active AI provider mode (AUTO, GROQ, or GEMINI)."""
    try:
        ai_provider_manager.set_mode(req.mode)
        return {"ok": True, "mode": ai_provider_manager.mode}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/providers/test")
async def test_providers() -> dict[str, Any]:
    """Execute live diagnostic pings to both Groq and Gemini."""
    return ai_provider_manager.test_all_providers()


@app.get("/api/check-target")
async def check_target(
    url: str,
    browser_check: bool = Query(default=False, description="Also perform a browser-level navigation check"),
) -> dict[str, Any]:
    """
    Perform reachability check on the given URL.

    Phase 2: Normalizes the URL before checking.
    Phase 3: If browser_check=true, performs a browser navigation check.

    Returns:
    {
        "reachable": bool,
        "browser_reachable": bool | None,
        "status_code": int | None,
        "original_url": str,
        "normalized_url": str,
        "final_url": str | None,
        "title": str | None,
        "content_available": bool | None,
        "error_type": str | None,
        "message": str | None,
    }
    """
    if not url or not url.strip():
        return {
            "reachable": False,
            "browser_reachable": None,
            "original_url": url or "",
            "normalized_url": "",
            "error_type": "invalid_url",
            "message": "URL is empty",
        }

    # ── Phase 2: Normalize URL ────────────────────────────────────────
    try:
        original_url, normalized_url = normalize_url(url)
    except ValueError as exc:
        return {
            "reachable": False,
            "browser_reachable": None,
            "original_url": url,
            "normalized_url": url,
            "error_type": "invalid_url",
            "message": str(exc),
        }

    base_result: dict[str, Any] = {
        "original_url": original_url,
        "normalized_url": normalized_url,
        "browser_reachable": None,
        "final_url": None,
        "title": None,
        "content_available": None,
    }

    # ── HTTP reachability probe ───────────────────────────────────────
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=4.0, pool=4.0),
            verify=False,  # Allow self-signed certs for local dev
        ) as client:
            response = await client.get(
                normalized_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            final_http_url = str(response.url)
            base_result.update({
                "reachable": True,
                "error_type": "reachable",
                "status_code": response.status_code,
                "final_url": final_http_url if final_http_url != normalized_url else normalized_url,
                "redirect_url": final_http_url if final_http_url != normalized_url else None,
            })
    except httpx.ConnectTimeout:
        base_result.update({
            "reachable": False, "error_type": "timeout", "message": "Connection timed out",
        })
        return base_result
    except httpx.ReadTimeout:
        base_result.update({
            "reachable": False, "error_type": "timeout", "message": "Read timed out",
        })
        return base_result
    except httpx.ConnectError as e:
        err = str(e).lower()
        if any(x in err for x in ("connection refused", "econnrefused", "10061", "all connection attempts failed")):
            etype = "connection_refused"
            emsg = "Connection refused"
        elif any(x in err for x in ("name or service not known", "getaddrinfo failed", "nodename nor servname")):
            etype = "dns_failure"
            emsg = "DNS resolution failed"
        else:
            etype = "unreachable"
            emsg = str(e)[:120]
        base_result.update({"reachable": False, "error_type": etype, "message": emsg})
        return base_result
    except httpx.TooManyRedirects:
        base_result.update({
            "reachable": False, "error_type": "redirect_failure", "message": "Too many redirects",
        })
        return base_result
    except Exception as e:
        err = str(e).lower()
        if "ssl" in err or "certificate" in err:
            etype = "ssl_error"
        else:
            etype = "unreachable"
        base_result.update({"reachable": False, "error_type": etype, "message": str(e)[:120]})
        return base_result

    # ── Phase 3: Optional browser-level check ────────────────────────
    if browser_check:
        browser_result = await _browser_preflight_check(normalized_url)
        base_result.update(browser_result)

    return base_result


async def _browser_preflight_check(url: str) -> dict[str, Any]:
    """
    Launch a real browser and navigate to the URL.
    Returns browser_reachable, title, final_url, content_available.
    Bounded to 15 seconds total.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=12_000)
                await page.wait_for_timeout(1500)

                final_url = page.url
                title = await page.title()
                try:
                    body_text = await page.locator("body").inner_text(timeout=2000)
                    body_len = len(body_text.strip())
                except Exception:
                    body_len = 0

                content_available = body_len > 80

                return {
                    "browser_reachable": True,
                    "final_url": final_url,
                    "title": title,
                    "content_available": content_available,
                    "body_text_length": body_len,
                }
            finally:
                await browser.close()

    except Exception as exc:
        err = classify_navigation_error(exc)
        return {
            "browser_reachable": False,
            "browser_error_type": err["error_type"],
            "browser_error_message": err["message"],
            "content_available": False,
        }


@app.post("/api/runs", response_model=RunState)
async def create_run(req: RunCreate) -> RunState:
    # ── Phase 15: URL scheme safety gate ─────────────────────────────
    raw_url = req.target_url or DEMO_URL
    try:
        original_url, normalized_url = normalize_url(raw_url)
    except ValueError as exc:
        raise HTTPException(422, detail=f"Invalid target_url: {exc}")

    run_id = uuid.uuid4().hex[:10]
    run = RunState(
        id=run_id,
        goal=req.goal,
        target_url=normalized_url,
        original_target_url=original_url,
        normalized_target_url=normalized_url,
        current_url=normalized_url,
        last_observed_url=normalized_url,
    )
    # Patch request to use normalized URL
    req_normalized = req.model_copy(update={"target_url": normalized_url})

    RUNS[run_id] = run
    await emit(
        run_id,
        Event(
            kind="queued",
            message=f"Autonomous QA run '{run_id}' queued (Mode: {ai_provider_manager.mode})",
        ),
    )
    task = asyncio.create_task(run_task(run_id, req_normalized))
    TASKS[run_id] = task
    return run


@app.get("/api/runs/{run_id}", response_model=RunState)
async def get_run(run_id: str) -> RunState:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/runs")
async def list_runs() -> list[RunState]:
    return list(RUNS.values())[-25:][::-1]


@app.get("/api/runs/{run_id}/events")
async def get_events(run_id: str) -> dict[str, Any]:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"events": run.events}


@app.get("/api/runs/{run_id}/report")
async def get_report(run_id: str) -> FileResponse:
    report = RUNS_DIR / run_id / "report.html"
    if not report.exists():
        raise HTTPException(404, "Audit report not ready yet")
    return FileResponse(report)


@app.get("/api/runs/{run_id}/report.json")
async def get_report_json(run_id: str) -> FileResponse:
    report = RUNS_DIR / run_id / "report.json"
    if not report.exists():
        raise HTTPException(404, "Report JSON not ready")
    return FileResponse(report, media_type="application/json")


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    task = TASKS.get(run_id)
    if task and not task.done():
        task.cancel()
        if run_id in RUNS:
            RUNS[run_id].status = "cancelled"
            RUNS[run_id].error = "User cancelled execution."
        return {"cancelled": True}
    return {"cancelled": False, "message": "Task already completed or not found"}
