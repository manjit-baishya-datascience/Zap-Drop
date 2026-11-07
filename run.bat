@echo off
title Zap Drop
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Zap Drop is not set up yet. Run setup.bat first.
    pause
    exit /b 1
)
rem Extra options can be passed through, e.g.  run.bat --port 9000 --no-browser
".venv\Scripts\python.exe" -m zapdrop %*
pause
