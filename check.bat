@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo  No virtual environment found. Run setup.bat first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m spikesight.selfcheck
pause
