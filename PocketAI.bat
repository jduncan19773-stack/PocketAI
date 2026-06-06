@echo off
REM ============================================================
REM  PocketAI launcher — opens the native app window.
REM  Uses the embedded Python on this drive (trusted by Windows),
REM  so it avoids the "unsigned exe blocked" permission problem.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set POCKETAI_DATA=%~dp0data
set POCKETAI_MODELS=%~dp0models
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPYCACHEPREFIX=%~dp0pycache

REM Find the embedded Python (pythonw.exe = no console window)
set PYW=
if exist "%~dp0python-embed\pythonw.exe" set PYW=%~dp0python-embed\pythonw.exe
if exist "%~dp0python-embed\python.exe"  set PY=%~dp0python-embed\python.exe

REM Fall back to venv if embedded Python missing
if "!PY!"=="" (
    if exist "%~dp0venv\Scripts\python.exe" set PY=%~dp0venv\Scripts\python.exe
    if exist "%~dp0venv\Scripts\pythonw.exe" set PYW=%~dp0venv\Scripts\pythonw.exe
)

if "!PY!"=="" (
    echo Python not found. Please run SETUP.bat first.
    pause
    exit /b 1
)

REM Launch the native window app with pythonw (no console window).
REM If pythonw is unavailable, fall back to python (shows a console).
if not "!PYW!"=="" (
    start "" "!PYW!" "%~dp0pocketai_app.py"
) else (
    "!PY!" "%~dp0pocketai_app.py"
)

endlocal
