@echo off
setlocal
cd /d "%~dp0"
title SpikeSight setup

echo.
echo   SpikeSight setup
echo   ==============
echo.
echo   This installs what SpikeSight needs. It takes about a minute.
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")

if not defined PY (
    echo   Python is not installed on this PC.
    echo.
    echo   SpikeSight needs Python 3.11 or newer. The download page will open
    echo   now - install it, tick "Add python.exe to PATH" on the first
    echo   screen, then run this setup again.
    echo.
    pause
    start "" "https://www.python.org/downloads/windows/"
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo   Creating a private Python environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

echo   Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo   Done.
echo.
echo   Next: run check.bat to confirm SpikeSight can see your Riot Client,
echo         then start.bat to launch it.
echo.
pause
exit /b 0

:fail
echo.
echo   Setup failed. The most common causes are no internet connection,
echo   or a Python older than 3.11. Check the messages above.
echo.
pause
exit /b 1
