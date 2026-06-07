"""
pocketai_app.py — App-window launcher for PocketAI.

Starts the FastAPI server in a background thread, waits for it to be ready,
then opens the UI in a clean, borderless **app window** using Microsoft Edge
(or Chrome) in --app mode. This looks like a standalone app — no tabs, no
address bar — but uses the rock-solid browser engine already on every Windows
machine, avoiding the fragile native-GUI toolkits.

When the window is closed, the server and the bundled AI engine are stopped.

Usage:  python pocketai_app.py     |     PocketAI.exe (PyInstaller)
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# When launched via pythonw.exe or a windowed (console-less) exe, sys.stdout
# and sys.stderr are None. Libraries like uvicorn write to them and crash.
# Redirect to devnull so the app runs cleanly with no console.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ── Paths / environment ──────────────────────────────────────────
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).parent
else:
    HERE = Path(__file__).resolve().parent

os.chdir(str(HERE))
os.environ["POCKETAI_DATA"]   = str(HERE / "data")
os.environ["POCKETAI_MODELS"] = str(HERE / "models")
os.environ["PYTHONUTF8"]      = "1"
if (HERE / "bin" / "llama-server.exe").exists():
    os.environ["POCKETAI_MODE"] = "usb"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PORT = int(os.environ.get("POCKETAI_PORT", "7860"))
URL  = f"http://127.0.0.1:{PORT}"

_LOG = HERE / "data" / "launch.log"

def _log(msg: str):
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ── Server (background thread) ───────────────────────────────────

def _run_server():
    try:
        _log("server thread: importing")
        import uvicorn
        from server.main import app
        _log("server thread: starting uvicorn")
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    except Exception:
        import traceback
        _log("server thread CRASHED:\n" + traceback.format_exc())


def _wait_for_server(timeout: int = 180) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/api/status", timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ── Browser app-window ───────────────────────────────────────────

def _find_browser() -> str | None:
    """Locate Edge or Chrome to host the app window."""
    pf   = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        os.path.join(pfx86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf,    r"Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
        os.path.join(pf,    r"Google\Chrome\Application\chrome.exe"),
        os.path.join(pfx86, r"Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            return exe
    return None


def _open_app_window(url: str):
    """
    Open `url` in a borderless app window. Returns the Popen handle if a
    dedicated browser process was launched (so we can wait on it), else None.
    """
    exe = _find_browser()
    if not exe:
        return None
    # A fresh user-data-dir forces a NEW browser process we can wait on,
    # so closing the window reliably ends PocketAI.
    profile = HERE / "data" / "_appwindow"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        return subprocess.Popen([
            exe,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,860",
        ])
    except Exception:
        return None


def _cleanup_and_exit(code: int = 0):
    """Stop the bundled AI engine (child processes) and exit."""
    try:
        import psutil
        me = psutil.Process()
        for child in me.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
    except Exception:
        pass
    os._exit(code)


# ── Main ─────────────────────────────────────────────────────────

def main():
    _log(f"main: frozen={getattr(sys,'frozen',False)} HERE={HERE} USB={os.environ.get('POCKETAI_MODE')}")
    threading.Thread(target=_run_server, daemon=True).start()
    ok = _wait_for_server(timeout=180)
    _log(f"main: server ready={ok}")

    browser = _find_browser()
    _log(f"main: browser={browser}")
    proc = _open_app_window(URL)
    _log(f"main: app window proc={proc.pid if proc else None}")
    if proc is not None:
        # Wait until the user closes the app window, then shut everything down
        try:
            proc.wait()
        except KeyboardInterrupt:
            pass
        _cleanup_and_exit(0)
    else:
        # No Edge/Chrome found — fall back to the default browser and stay alive
        import webbrowser
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            _cleanup_and_exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # The packaged app is windowed (no console); record any startup crash
        # so it can be diagnosed instead of vanishing silently.
        try:
            import traceback
            log = HERE / "data" / "startup_error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise
