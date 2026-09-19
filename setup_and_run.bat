@echo off
setlocal
cd /d "%~dp0"

echo.
echo  ======================================================
echo   TRACE//QA  -  Dual-Provider Autonomous UI QA
echo   Providers: Groq (LPU) + Google Gemini (TPU)
echo  ======================================================
echo.

REM 1. Check Python installation
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
  set "PY_CMD=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL% equ 0 (
    set "PY_CMD=python"
  ) else (
    echo [ERROR] Python 3 was not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
  )
)

REM Check Node.js runtime (optional)
where node >nul 2>&1
if %ERRORLEVEL% equ 0 (
  echo [+] Node.js detected.
) else (
  echo [-] Node.js not detected - optional, Python backend is used.
)

REM 2. Setup Virtual Environment
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating Python virtual environment...
  %PY_CMD% -m venv .venv
  if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )
)

REM 3. Dependencies and Playwright Chromium
echo [2/4] Verifying dependencies and Playwright browser...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
call ".venv\Scripts\python.exe" -m playwright install chromium >nul 2>&1

REM 4. Ensure .env exists
if not exist ".env" (
  echo Creating initial .env from template...
  copy /y ".env.example" ".env" >nul
)

REM 5. Audit AI Providers (without exposing secrets)
echo [3/4] Auditing AI Provider Configuration...
call ".venv\Scripts\python.exe" -m backend.audit_env

REM 6. Launch Server & Browser
echo.
echo [4/4] Starting TRACE//QA server on http://127.0.0.1:8000 ...
echo       (Keep this window open while using TRACE//QA. Press Ctrl+C to stop.)
echo.
start "" "http://127.0.0.1:8000"
call ".venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --loop none
