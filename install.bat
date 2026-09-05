@echo off
rem Install python dependencies into local virtual environment (.venv)
rem Prerequisite: python (3.10+) available in PATH
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [install] creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [install] ERROR: failed to create venv. Make sure python is in PATH.
        pause
        exit /b 1
    )
)

echo [install] upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [install] ERROR: pip upgrade failed.
    pause
    exit /b 1
)

echo [install] installing requirements ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [install] ERROR: install requirements failed. Check network / proxy settings.
    pause
    exit /b 1
)

echo [install] done.
echo.
echo Next: run start.bat to launch the MCP server.
pause
