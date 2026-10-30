@echo off
rem Starts JARVIS with the holographic HUD as the main screen.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating Python virtual environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
if not exist hud\dist\index.html (
  echo Building the HUD ^(needs Node.js^)...
  pushd hud
  call npm install || goto :error
  call npm run build || goto :error
  popd
)
.venv\Scripts\python main.py --hud
goto :eof

:error
echo.
echo Setup failed. See the message above.
pause
