from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent import AutonomousTester
from .ai_providers import ai_provider_manager
from .config import DEMO_URL, ROOT, RUNS_DIR
from .models import Event, RunCreate, RunState

app = FastAPI(title="TRACE//QA", version="2.0.0")
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


@app.post("/api/runs", response_model=RunState)
async def create_run(req: RunCreate) -> RunState:
    run_id = uuid.uuid4().hex[:10]
    run = RunState(
        id=run_id,
        goal=req.goal,
        target_url=req.target_url or DEMO_URL,
    )
    RUNS[run_id] = run
    await emit(run_id, Event(kind="queued", message=f"Autonomous QA run '{run_id}' queued (Mode: {ai_provider_manager.mode})"))
    task = asyncio.create_task(run_task(run_id, req))
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
            RUNS[run_id].status = "failed"
            RUNS[run_id].error = "User cancelled execution."
        return {"cancelled": True}
    return {"cancelled": False, "message": "Task already completed or not found"}
