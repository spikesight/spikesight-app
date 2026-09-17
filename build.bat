@echo off
setlocal
cd /d "%~dp0"
title SpikeSight - build the standalone app

if not exist ".venv\Scripts\python.exe" (
    echo   Run setup.bat first.
    pause
    exit /b 1
)

echo   Installing build tools...
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller Pillow
if errorlevel 1 goto :fail

echo   Drawing the icon...
".venv\Scripts\python.exe" tools\make_icon.py
if errorlevel 1 goto :fail

echo   Building SpikeSight.exe (this takes a minute)...
".venv\Scripts\python.exe" toolsuild_exe.py
if errorlevel 1 goto :fail

echo.
echo   Done. Share the zip in the dist folder - it needs no Python.
echo.
pause
exit /b 0

:fail
echo.
echo   Build failed. See the messages above.
echo.
pause
exit /b 1
