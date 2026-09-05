@echo off
rem Start the volc-aibot service (windowless via pythonw + system tray)
rem Same port serves BOTH: web config page (http://127.0.0.1:PORT/) and MCP SSE
rem Default port: read from global config (C:\Users\%USERNAME%\.volcengine_aibot_scripts\global.json)
rem Override: start.bat 19000
rem Prerequisites:
rem   1. run install.bat first
rem   2. chrome_capture_operate service running (default http://127.0.0.1:33445)
rem      and daily Chrome logged in to volcengine console (cookie pushed)
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Build argument list (empty PORT must not become an empty string arg)
set ARGS=-X utf8 mcp_server.py
set CHECKPORT=19000
if not "%~1"=="" (
    set ARGS=!ARGS! --port %~1
    set CHECKPORT=%~1
)

rem Port pre-check: a second instance cannot bind and would exit silently
rem (pythonw has no console). Stop the running one from the system tray
rem (right-click -> exit) first, or pass a different port.
netstat -ano | findstr ":!CHECKPORT! " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [start] ERROR: port !CHECKPORT! is already in use.
    echo [start] A service instance is probably already running.
    echo [start] Use the system tray icon ^(right-click -^> exit^) to stop it,
    echo [start] or run: start.bat ^<another_port^>
    echo.
    pause
    exit /b 1
)

echo [start] launching service (windowless, tray icon)
echo [start] config page: http://127.0.0.1:!CHECKPORT!/
echo [start] MCP SSE:     http://127.0.0.1:!CHECKPORT!/sse
echo [start] tray: double-click to open page, right-click to exit

rem Launch detached with output redirected to startup logs
rem (cmd "start" cannot pass output redirection, so use powershell)
if not exist log mkdir log
powershell -NoProfile -Command "Start-Process -FilePath '.venv\Scripts\pythonw.exe' -WindowStyle Hidden -ArgumentList '!ARGS!' -RedirectStandardOutput 'log\startup.log' -RedirectStandardError 'log\startup_err.log'" >nul 2>&1

rem Startup self-check: wait up to 15s for the port to listen
set STARTED=0
for /l %%i in (1,1,15) do (
    if !STARTED!==0 (
        netstat -ano | findstr ":!CHECKPORT! " | findstr "LISTENING" >nul 2>&1
        if !errorlevel!==0 (
            set STARTED=1
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if "!STARTED!"=="1" (
    echo [start] service started OK ^(port !CHECKPORT! listening, tray icon active^).
    echo [start] you can close this window now.
    timeout /t 3 >nul
) else (
    echo [start] WARNING: port !CHECKPORT! is NOT listening after 15 seconds.
    echo [start] The service failed to start. Check the logs:
    echo [start]   log\startup.log  and  log\startup_err.log
    echo [start] Also make sure chrome_capture_operate is running
    echo [start] ^(default http://127.0.0.1:33445^) and Chrome is logged in.
    echo.
    type log\startup_err.log 2>nul
    echo.
    pause
    exit /b 1
)
endlocal
