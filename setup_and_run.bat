@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo  ======================================================
echo   TRACE//QA  -  Dual-Provider Autonomous UI QA
echo   Providers: Groq (LPU) + Google Gemini (TPU)
echo  ======================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python was not found. Please install Python 3.11+ and add to PATH.
    pause
    exit /b 1
  ) else (
    set "PY_CMD=python"
  )
) else (
  set "PY_CMD=py -3"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creating Python virtual environment...
  %PY_CMD% -m venv .venv
)

echo [2/5] Checking Python dependencies...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Dependency check failed. Retrying in verbose mode...
  call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo [3/5] Verifying Playwright Chromium browser...
call ".venv\Scripts\python.exe" -m playwright install chromium >nul 2>&1

if not exist ".env" (
  echo Creating initial .env from template...
  copy /y ".env.example" ".env" >nul
)

echo [4/5] Auditing AI Provider Configuration...
set "GROQ_KEY="
set "GEMINI_KEY="

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  if "%%A"=="GROQ_API_KEY" set "GROQ_KEY=%%B"
  if "%%A"=="GEMINI_API_KEY" set "GEMINI_KEY=%%B"
)

set "CONFIGURED_COUNT=0"

if not "!GROQ_KEY!"=="" (
  echo   [+] Groq Provider: CONFIGURED
  set /a CONFIGURED_COUNT+=1
) else (
  echo   [!] Groq Provider: NOT CONFIGURED
)

if not "!GEMINI_KEY!"=="" (
  echo   [+] Google Gemini Provider: CONFIGURED
  set /a CONFIGURED_COUNT+=1
) else (
  echo   [!] Google Gemini Provider: NOT CONFIGURED
)

if !CONFIGURED_COUNT! EQU 0 (
  echo.
  echo [!] Neither Groq nor Gemini keys were found in .env.
  echo     Please enter at least one API key to enable autonomous testing.
  set /p "USER_GROQ=Enter Groq API key (or press Enter to skip): "
  if not "!USER_GROQ!"=="" (
    powershell -NoProfile -Command "(Get-Content '.env') -replace '^GROQ_API_KEY=.*$','GROQ_API_KEY=' + $env:USER_GROQ | Set-Content '.env'"
    set /a CONFIGURED_COUNT+=1
  )
  set /p "USER_GEMINI=Enter Gemini API key (or press Enter to skip): "
  if not "!USER_GEMINI!"=="" (
    powershell -NoProfile -Command "(Get-Content '.env') -replace '^GEMINI_API_KEY=.*$','GEMINI_API_KEY=' + $env:USER_GEMINI | Set-Content '.env'"
    set /a CONFIGURED_COUNT+=1
  )
)

if !CONFIGURED_COUNT! EQU 1 (
  echo.
  echo ---------------------------------------------------------------
  echo  WARNING: Only one AI provider is configured.
  echo  TRACE//QA will start in DEGRADED mode with single-provider failover.
  echo ---------------------------------------------------------------
) else (
  echo.
  echo   Dual-provider architecture ready (AUTO failover active).
)

echo.
echo [5/5] Launching TRACE//QA on http://127.0.0.1:8000 ...
start "TRACE//QA Server" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
start "TRACE//QA" http://127.0.0.1:8000

echo.
echo TRACE//QA is running.
echo Keep this terminal window open while using the application.
echo.
