@echo off
setlocal EnableDelayedExpansion
title PocketAI Setup
color 0E

echo.
echo  ============================================================
echo    ◈  PocketAI — First-Time USB Setup
echo  ============================================================
echo.
echo  This will download the AI engine and models (~5-6 GB total).
echo  You only need to do this once.
echo  After setup, PocketAI runs 100%% offline.
echo.

cd /d "%~dp0"

REM ── Quick mode: just install Python deps, skip downloads ─────────────────
if "%1"=="--quick" goto :quick_setup

REM ── Check for Python ─────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed.
    echo  Download it from python.org then re-run this setup.
    echo.
    pause
    exit /b 1
)

REM ── Set up virtual environment ────────────────────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo  Creating Python environment on this drive...
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Virtual environment creation failed.
        pause
        exit /b 1
    )
)

echo  Installing Python dependencies...
venv\Scripts\pip install -r requirements.txt -q
if errorlevel 1 (
    echo  ERROR: Dependency installation failed.
    pause
    exit /b 1
)
echo  Dependencies ready.
echo.

REM ── Ask about machine memory ──────────────────────────────────────────────
set /p DUAL="  Does this computer have 16 GB RAM or more? (y/n): "
if /i "!DUAL!"=="y" (
    echo  Downloading full setup ^(phi4-mini + qwen3-4b^)...
    venv\Scripts\python setup_download.py
) else (
    echo  Downloading single-model setup ^(phi4-mini only^)...
    venv\Scripts\python setup_download.py --no-secondary
)

echo.
echo  ============================================================
echo   ✓  Setup complete!
echo.
echo   To start PocketAI: double-click START_POCKETAI.bat
echo  ============================================================
echo.
pause
exit /b 0

REM ── Quick setup (just deps, called from START_POCKETAI.bat) ──────────────
:quick_setup
if not exist "venv\Scripts\python.exe" (
    python -m venv venv
)
venv\Scripts\pip install -r requirements.txt -q
exit /b 0
