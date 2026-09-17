@echo off
setlocal
cd /d "%~dp0"
title SpikeSight

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo   SpikeSight is not set up yet. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

rem pythonw keeps the console hidden - the app opens its own window, and
rem closing that window quits SpikeSight.
start "" ".venv\Scripts\pythonw.exe" -m spikesight %*
