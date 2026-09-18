@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  echo Starting TRACE frontend at http://127.0.0.1:4183/
  start "TRACE Frontend" cmd /k "py -m http.server 4183"
) else (
  echo Python was not found. Open index.html directly or serve this folder with a static server.
  pause
)
endlocal
