@echo off
REM ============================================================
REM  PocketAI launcher.
REM  Runs python.exe directly (reliable). A small console window
REM  stays open while PocketAI runs -- you can minimize it.
REM  PocketAI itself opens in its own app window. Closing that
REM  app window also closes this launcher.
REM ============================================================
setlocal EnableDelayedExpansion
title PocketAI
cd /d "%~dp0"

set POCKETAI_DATA=%~dp0data
set POCKETAI_MODELS=%~dp0models
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPYCACHEPREFIX=%~dp0pycache

REM ── Find python.exe (prefer the embedded one on the drive) ──────────────
set PY=
if exist "%~dp0python-embed\python.exe" set PY=%~dp0python-embed\python.exe
if "!PY!"=="" if exist "%~dp0venv\Scripts\python.exe" set PY=%~dp0venv\Scripts\python.exe
if "!PY!"=="" (
    where python >nul 2>&1 && set PY=python
)
if "!PY!"=="" (
    echo Python not found. Please run SETUP.bat first.
    pause
    exit /b 1
)

echo.
echo   PocketAI is starting...  (your app window will open shortly)
echo   You can minimize this window. Closing the app window stops PocketAI.
echo.

"!PY!" "%~dp0pocketai_app.py"

endlocal
