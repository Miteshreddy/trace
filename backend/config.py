from __future__ import annotations

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

APP_NAME = os.getenv("APP_NAME", "TRACE//QA")

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
).strip()

# Local Google Gemma (offline mode)
GOOGLE_LOCAL_MODEL = os.getenv("GOOGLE_LOCAL_MODEL", "google/gemma-3-1b-it").strip()

# Selection Mode: AUTO, GROQ, GEMINI, LOCAL, OFFLINE
AI_PROVIDER_MODE = os.getenv("AI_PROVIDER_MODE", "AUTO").strip().upper()

# Server & Target
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
DEMO_URL = os.getenv("DEMO_URL", f"http://{HOST}:{PORT}/demo/")

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
