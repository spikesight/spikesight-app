@echo off
setlocal
cd /d "%~dp0"
title SpikeSight (console)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   SpikeSight is not set up yet. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

rem Same app, but with the log visible. Use this when reporting a problem.
".venv\Scripts\python.exe" -m spikesight --verbose %*
if errorlevel 1 pause
