@echo off
setlocal EnableDelayedExpansion
title PocketAI — Starting up...
color 0B
chcp 65001 >nul 2>&1

echo.
echo  ============================================================
echo   PocketAI — Your Private AI Assistant
echo  ============================================================
echo.

cd /d "%~dp0"

REM ── Keep everything on this drive — no host machine footprint ─────────────
set POCKETAI_DATA=%~dp0data
set POCKETAI_MODELS=%~dp0models
set PYTHONDONTWRITEBYTECODE=1
set PYTHONPYCACHEPREFIX=%~dp0pycache
set OLLAMA_HOME=%~dp0ollama_home
set OLLAMA_MODELS=%~dp0models
set OLLAMA_MAX_LOADED_MODELS=2
set OLLAMA_NUM_PARALLEL=2
set OLLAMA_FLASH_ATTENTION=1

REM ── Find Python ──────────────────────────────────────────────────────────
REM Priority: embedded Python on this drive > venv > system Python
set PYTHON=

REM 1. Embedded Python on the drive (no installation needed)
if exist "%~dp0python-embed\python.exe" (
    set PYTHON=%~dp0python-embed\python.exe
    goto :python_found
)

REM 2. Virtual environment on the drive (created on a previous run)
if exist "%~dp0venv\Scripts\python.exe" (
    set PYTHON=%~dp0venv\Scripts\python.exe
    goto :python_found
)

REM 3. System Python (must be installed on this PC)
where python >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :python_found
)

REM 4. Python in common install locations
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%P (
        set PYTHON=%%P
        goto :python_found
    )
)

REM No Python found anywhere
echo  ============================================================
echo   Python is not installed on this computer.
echo.
echo   To fix this:
echo     Option 1: Run SETUP.bat (downloads Python automatically)
echo     Option 2: Download Python free from python.org
echo.
echo   Then double-click START_POCKETAI.bat again.
echo  ============================================================
echo.
pause
exit /b 1

:python_found
echo  Using Python: !PYTHON!

REM ── If using system Python: set up venv once ─────────────────────────────
if "!PYTHON!"=="python" (
    if not exist "%~dp0venv\Scripts\python.exe" (
        echo  Setting up PocketAI for first use ^(about 1 minute^)...
        python -m venv "%~dp0venv"
        if errorlevel 1 (
            echo  Setup failed. Please run SETUP.bat or contact support.
            pause
            exit /b 1
        )
        "%~dp0venv\Scripts\pip" install -r "%~dp0requirements.txt" -q
        echo  Ready!
        echo.
    )
    set PYTHON=%~dp0venv\Scripts\python.exe
)

REM ── Check dependencies are installed ─────────────────────────────────────
"!PYTHON!" -c "import fastapi, uvicorn, httpx, aiosqlite" >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies...
    "!PYTHON!" -m pip install -r "%~dp0requirements.txt" -q 2>&1
)

REM ── Detect mode ──────────────────────────────────────────────────────────
set MODE=ollama
set USB_FLAG=

if exist "%~dp0bin\llama-server.exe" (
    set MODE=usb
    set USB_FLAG=--usb
    echo  Mode: USB ^(built-in AI engine — no internet needed^)
) else (
    echo  Mode: Ollama
)
echo.

REM ── In Ollama mode: start Ollama if it is not running ────────────────────
if "!MODE!"=="ollama" (
    "!PYTHON!" -c "import httpx; r=httpx.get('http://localhost:11434/api/tags',timeout=2); exit(0 if r.status_code==200 else 1)" >nul 2>&1
    if errorlevel 1 (
        echo  Starting AI service...
        REM Try known Ollama locations
        for %%O in (
            "%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
            "%ProgramFiles%\Ollama\ollama.exe"
            "ollama.exe"
        ) do (
            if exist %%O (
                start "" /B %%O serve >nul 2>&1
                goto :ollama_started
            )
        )
        where ollama >nul 2>&1
        if not errorlevel 1 (
            start "" /B ollama serve >nul 2>&1
        )
        :ollama_started
        echo  Waiting for AI service to start...
        for /L %%i in (1,1,15) do (
            timeout /t 1 /nobreak >nul
            "!PYTHON!" -c "import httpx; r=httpx.get('http://localhost:11434/api/tags',timeout=1); exit(0 if r.status_code==200 else 1)" >nul 2>&1
            if not errorlevel 1 goto :ollama_ready
        )
        echo.
        echo  ============================================================
        echo   Could not connect to the AI service.
        echo.
        echo   Please install Ollama from ollama.com (free, takes 2 min)
        echo   Or run SETUP.bat to use the built-in AI engine instead.
        echo  ============================================================
        echo.
        pause
        exit /b 1
        :ollama_ready
        echo  AI service ready.
        echo.
    ) else (
        echo  AI service: running
        echo.
    )
)

REM ── Launch PocketAI ───────────────────────────────────────────────────────
title PocketAI
echo  Starting PocketAI — your browser will open automatically.
echo  Press Ctrl+C or close this window to stop.
echo.
"!PYTHON!" "%~dp0run.py" !USB_FLAG!

if errorlevel 1 (
    echo.
    echo  PocketAI stopped unexpectedly.
    echo  If you see an error above, please note it and run SETUP.bat.
    echo.
    pause
)
endlocal
