@echo off
setlocal EnableDelayedExpansion
title PocketAI Setup
color 0E
chcp 65001 >nul 2>&1

echo.
echo  ============================================================
echo   PocketAI — One-Time Setup
echo  ============================================================
echo.
echo  This downloads the AI software and models.
echo  It only runs once and takes 10-20 minutes depending on
echo  your internet speed. Total download: about 5 GB.
echo.
echo  After this, PocketAI works forever with no internet.
echo.

cd /d "%~dp0"

REM ── Quick mode: just install Python deps ─────────────────────────────────
if "%1"=="--quick" goto :quick_only

REM ── Find Python to run the download script ───────────────────────────────
set PYTHON=

if exist "%~dp0python-embed\python.exe" (
    set PYTHON=%~dp0python-embed\python.exe
    goto :have_python
)
if exist "%~dp0venv\Scripts\python.exe" (
    set PYTHON=%~dp0venv\Scripts\python.exe
    goto :have_python
)
where python >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :have_python
)
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%P ( set PYTHON=%%P & goto :have_python )
)

REM No Python at all — we need it to run the download script
echo  Python is not installed. We need Python to run the setup.
echo.
echo  Please do ONE of the following:
echo    1. Download Python (free) from python.org
echo       then run SETUP.bat again.
echo    2. Or ask whoever gave you this drive to run setup
echo       on a computer that already has Python installed.
echo.
pause
exit /b 1

:have_python
echo  Using: !PYTHON!
echo.

REM ── Set up venv if not using embedded Python ─────────────────────────────
if not exist "%~dp0python-embed\python.exe" (
    if not exist "%~dp0venv\Scripts\python.exe" (
        echo  Creating Python environment on this drive...
        "!PYTHON!" -m venv "%~dp0venv"
        set PYTHON=%~dp0venv\Scripts\python.exe
    )
)

REM ── Install pip requirements first so the download script can run ─────────
echo  Installing core dependencies...
"!PYTHON!" -m pip install -r "%~dp0requirements.txt" httpx -q 2>&1
echo.

REM ── Ask about machine memory ──────────────────────────────────────────────
echo  Quick question:
set /p BIGMEM="  Does this computer have 16 GB of RAM or more? (y/n): "
echo.

if /i "!BIGMEM!"=="y" (
    echo  Full setup: downloading both AI models...
    "!PYTHON!" "%~dp0setup_download.py"
) else (
    echo  Minimal setup: downloading one AI model (good for 8 GB machines)...
    "!PYTHON!" "%~dp0setup_download.py" --minimal
)

echo.
echo  ============================================================
echo   Setup complete!
echo.
echo   To start PocketAI:
echo   --^> Double-click START_POCKETAI.bat
echo  ============================================================
echo.
pause
exit /b 0

REM ── Quick install: just Python deps, no downloads ─────────────────────────
:quick_only
set PYTHON=python
if exist "%~dp0python-embed\python.exe" set PYTHON=%~dp0python-embed\python.exe
if exist "%~dp0venv\Scripts\python.exe" set PYTHON=%~dp0venv\Scripts\python.exe
if not exist "%~dp0python-embed\python.exe" (
    if not exist "%~dp0venv\Scripts\python.exe" (
        python -m venv "%~dp0venv" >nul 2>&1
        set PYTHON=%~dp0venv\Scripts\python.exe
    )
)
"!PYTHON!" -m pip install -r "%~dp0requirements.txt" -q 2>&1
exit /b 0
