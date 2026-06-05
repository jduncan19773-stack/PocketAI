@echo off
setlocal EnableDelayedExpansion
title PocketAI
color 0B

echo.
echo  ============================================================
echo    ◈  PocketAI  ^|  Your Private AI Assistant
echo  ============================================================
echo.

cd /d "%~dp0"

REM ── Zero-footprint env vars — all reads/writes stay on this drive ─────────
set POCKETAI_DATA=%~dp0data
set POCKETAI_MODELS=%~dp0models
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPYCACHEPREFIX=%~dp0pycache

REM Ollama env vars (used when Ollama is the backend)
set OLLAMA_HOME=%~dp0ollama_home
set OLLAMA_MODELS=%~dp0models
set OLLAMA_MAX_LOADED_MODELS=2
set OLLAMA_NUM_PARALLEL=2
set OLLAMA_FLASH_ATTENTION=1

REM ── Check for Python ─────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed on this computer.
    echo.
    echo  Please install Python from python.org — it's free and takes 2 minutes.
    echo  Then plug this drive back in and double-click START_POCKETAI again.
    echo.
    pause
    exit /b 1
)

REM ── Set up virtual environment on the USB (first run only) ───────────────
if not exist "venv\Scripts\python.exe" (
    echo  First run — setting up PocketAI ^(about 1 minute^)...
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Could not create virtual environment.
        pause
        exit /b 1
    )
    venv\Scripts\pip install -r requirements.txt -q
    if errorlevel 1 (
        echo  ERROR: Could not install dependencies.
        pause
        exit /b 1
    )
    echo  Setup complete!
    echo.
)

set PYTHON=venv\Scripts\python.exe

REM ── Choose launch mode ────────────────────────────────────────────────────
REM If llama-server.exe is present → USB mode (no Ollama needed)
REM Otherwise → Ollama mode (Ollama must be installed/running on this PC)

if exist "bin\llama-server.exe" (
    echo  USB mode: using bundled AI engine ^(no internet needed^)
    echo.
    set POCKETAI_MODE=usb
    %PYTHON% run.py --usb
) else (
    echo  Ollama mode: connecting to local Ollama service
    echo.
    %PYTHON% start.py
)

if errorlevel 1 (
    echo.
    echo  PocketAI exited with an error — see message above.
    pause
)
endlocal
