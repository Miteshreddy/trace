# TRACE//QA

Autonomous, black-box UI/UX and accessibility testing framework powered by a dual-engine multimodal AI provider architecture (**Groq** + **Google Gemini**).

---

## What It Does

TRACE//QA accepts high-level natural language user goals and autonomously navigates target applications like a real human user. It relies exclusively on black-box perception—screenshots, generic visible element geometry, and native accessibility trees—without requiring embedded SDKs, test IDs, or source-code alterations.

The framework discovers usability friction, maps multi-path user journeys, detects UI/UX regressions, audits WCAG 2.1 AA accessibility compliance, and generates executive-grade visual reports.

---

## Central AI Provider Architecture

All agent reasoning, visual navigation, and auditing tasks are routed through a centralized provider abstraction layer:

```text
               ┌───────────────────────────────┐
               │    Frontend SaaS Dashboard    │
               └───────────────┬───────────────┘
                               │
               ┌───────────────▼───────────────┐
               │   FastAPI Backend & Router    │
               └───────────────┬───────────────┘
                               │
               ┌───────────────▼───────────────┐
               │      Agent Orchestrator       │
               └───────────────┬───────────────┘
                               │
               ┌───────────────▼───────────────┐
               │      AI Provider Manager      │
               │   (AUTO Mode / Failover /     │
               │    Health & Latency Tracking) │
               └───────┬───────────────┬───────┘
                       │               │
        ┌──────────────▼──────┐ ┌──────▼──────────────┐
        │    Groq Provider    │ │   Gemini Provider   │
        │  (Qwen 3.8 27B LPU) │ │(Gemini 2.5 Flash TPU│
        └─────────────────────┘ └─────────────────────┘
                               │
               ┌───────────────▼───────────────┐
               │    Action Validator Safety    │
               └───────────────┬───────────────┘
                               │
               ┌───────────────▼───────────────┐
               │  Playwright Browser Runner    │
               │  (Coordinate Clicks / Typing) │
               └───────────────────────────────┘
```

The rest of the framework interacts strictly with `AIProviderManager` (`decide_action`, `audit_run`, `ping`) rather than direct provider SDKs.

---

## Provider Modes & Intelligent Routing

| Mode | Routing Behavior | Failover Strategy |
|---|---|---|
| **AUTO** (Default) | Alternates requests between healthy providers (Groq & Gemini) for balanced utilization. | If the active provider errors or times out, immediately fails over to the secondary provider without dropping the turn. |
| **GROQ** | Exclusively routes decisions and audits to Groq (`qwen/qwen3.8-27b`). | If Groq is degraded or unconfigured, falls back gracefully with a clear telemetry warning. |
| **GEMINI** | Exclusively routes decisions and audits to Google Gemini (`gemini-2.5-flash`). | If Gemini is degraded or unconfigured, falls back gracefully with a clear telemetry warning. |

---

## Quick Start on Windows

1. Double-click or run `setup_and_run.bat`.
2. The launcher will automatically verify Python 3.11+, install dependencies, ensure Chromium is installed, and audit `.env` keys.
3. If only one provider is configured, the application launches in degraded mode while keeping the configured provider active.
4. Wait for the browser to open `http://127.0.0.1:8000`.
5. Select a **Preset Scenario** (e.g. *"1. Blue shoe under $100"*) and click **Run Autonomous Audit**.

---

## Environment Variables (.env)

Configuration is loaded from `.env` (which is included in `.gitignore` to prevent leaking keys):

```ini
# Groq Configuration
GROQ_API_KEY=gsk_...
GROQ_MODEL=qwen/qwen3.8-27b
GROQ_BASE_URL=https://api.groq.com/openai/v1

# Google Gemini Configuration
GEMINI_API_KEY=AQ...
GEMINI_MODEL=gemini-2.5-flash
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# Provider Selection (AUTO, GROQ, or GEMINI)
AI_PROVIDER_MODE=AUTO

# Server & Target
HOST=127.0.0.1
PORT=8000
DEMO_URL=http://127.0.0.1:8000/demo/
```

---

## Diagnostic Probing ("Test AI Providers")

From the UI dashboard or via the API (`POST /api/providers/test`), you can trigger an on-demand diagnostic probe. This executes a sub-second ping to both Groq and Google Gemini, reporting connectivity and round-trip latency without exposing secret keys:

```json
{
  "mode": "AUTO",
  "results": {
    "groq": {
      "name": "groq",
      "model": "qwen/qwen3.8-27b",
      "configured": true,
      "connected": true,
      "latency_ms": 1069.5,
      "message": "Connected"
    },
    "gemini": {
      "name": "gemini",
      "model": "gemini-2.5-flash",
      "configured": true,
      "connected": true,
      "latency_ms": 1462.1,
      "message": "Connected"
    }
  }
}
```

---

## Safety & Action Validation Layer

Before any model output is executed by the Playwright driver, it passes through `ActionValidator`:
- Rejects arbitrary code or unsanitized strings (`<script>`, `javascript:`).
- Confirms target coordinates or element IDs exist in the observable DOM.
- Clamps scroll amounts and validates allowed keyboard keys.
- Normalizes decision dictionaries, preventing malformed LLM responses from causing browser crashes.

---

## Project Structure

```text
backend/
├── ai_providers/         # Central provider abstraction
│   ├── __init__.py
│   ├── base.py           # AIProvider abstract base & ProviderHealth dataclass
│   ├── groq_provider.py  # Groq LPU provider implementation
│   ├── gemini_provider.py# Google Gemini TPU provider implementation
│   └── manager.py        # Central manager with AUTO failover & health telemetry
├── action_validator.py   # Safety validation & action sanitizer pipeline
├── agent.py              # Autonomous tester orchestrator & multi-path graph builder
├── auditor.py            # WCAG 2.1 AA contrast, touch target, and UX friction scoring
├── browser.py            # Playwright coordinate-based runner & visual action annotator
├── config.py             # Environment configuration loader
├── models.py             # Pydantic data schemas
└── report.py             # Standalone executive HTML report generator
frontend/
├── index.html            # Dark-mode dashboard with AI engine controls
├── styles.css            # Cyberpunk / Linear-inspired styling system
└── app.js                # Real-time state polling, mode switcher, and live scrubber
demo/
└── index.html            # Target testbed store with intentional UX and a11y defects
data/runs/                # Annotated screenshots and generated audit reports per run
```

---

## Troubleshooting

- **Rate Limit (413 / 429) on Groq**: The Groq provider automatically catches rate limits, downscales images, backs off, and fails over to Gemini.
- **Missing Gemini API Key**: The launcher will output a warning and operate in degraded single-provider mode using Groq.
- **Port 8000 in use**: Modify `PORT=8080` in `.env` and restart.
