@echo off
rem Starts JARVIS in the terminal (type, or 'v' for push-to-talk).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating Python virtual environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
.venv\Scripts\python main.py
goto :eof

:error
echo.
echo Setup failed. See the message above.
pause
