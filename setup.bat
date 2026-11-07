@echo off
setlocal
title Zap Drop setup
cd /d "%~dp0"

echo.
echo  Zap Drop setup
echo  ==============
echo.

rem --- find a working Python 3.10+ (the Microsoft Store "python" stub does not count)
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
    echo  Python 3.10 or newer was not found on this PC.
    echo.
    choice /M " Install Python 3.13 now using winget"
    if errorlevel 2 (
        echo.
        echo  Install Python from https://www.python.org/downloads/
        echo  Tick "Add python.exe to PATH" in the installer, then run setup.bat again.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements
    echo.
    echo  Python installed. Close this window and run setup.bat again
    echo  so Windows picks up the new PATH.
    pause
    exit /b 0
)

echo  Using: & %PY% --version
if not exist ".venv\Scripts\python.exe" (
    echo  Creating virtual environment...
    %PY% -m venv .venv || goto :fail
)
echo  Installing packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

echo.
echo  Done.
echo  1. Run firewall.bat once (needs admin) so the other PC can reach this one.
echo  2. Start Zap Drop with run.bat
echo.
pause
exit /b 0

:fail
echo.
echo  Setup failed. See the messages above.
pause
exit /b 1
