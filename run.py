"""
run.py — PocketAI launcher.

Checks for the inference backend, starts the FastAPI server,
then opens the browser automatically.

Usage:
  python run.py            # Ollama mode (default) — Ollama must be running
  python run.py --usb      # USB mode — starts bundled llama-server.exe instances

Modes
-----
Ollama mode (default):
  Assumes Ollama is already running at http://localhost:11434 with the right
  models pulled. Just starts uvicorn and opens the browser.

USB mode (--usb or POCKETAI_MODE=usb):
  Expects bin/llama-server.exe and models/*.gguf on the drive.
  The FastAPI startup event (server/launcher.py) will start llama-server
  instances automatically before accepting requests.
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

HERE = Path(__file__).parent
PORT = int(os.environ.get("POCKETAI_PORT", "7860"))


# ── CLI arguments ────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="PocketAI launcher")
parser.add_argument("--usb",  action="store_true", help="Enable USB / llama-server mode")
parser.add_argument("--port", type=int, default=PORT, help=f"Port (default {PORT})")
args, _ = parser.parse_known_args()

if args.usb:
    os.environ["POCKETAI_MODE"] = "usb"
PORT = args.port


# ── Ollama readiness check ───────────────────────────────────────

_OLLAMA_WIN_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"
)

def ollama_running() -> bool:
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _start_ollama_if_needed():
    """Start Ollama if it's installed but the service isn't running yet."""
    import subprocess, shutil
    exe = shutil.which("ollama") or (_OLLAMA_WIN_PATH if os.path.exists(_OLLAMA_WIN_PATH) else None)
    if exe and not ollama_running():
        print("  [*]  Starting Ollama service...")
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # Give it a few seconds to come up
        for _ in range(10):
            import time
            time.sleep(1)
            if ollama_running():
                print("  [*]  Ollama ready.")
                return
        print("  WARNING  Ollama did not start in time.")


def check_ollama():
    if os.environ.get("POCKETAI_MODE") == "usb":
        return   # llama-server is started by the app itself
    if not ollama_running():
        _start_ollama_if_needed()   # try to auto-start if installed
    if not ollama_running():
        print()
        print("  ╔══════════════════════════════════════════════════════╗")
        print("  ║  Ollama is not running and could not be started.     ║")
        print("  ║                                                      ║")
        print("  ║  Options:                                            ║")
        print("  ║  1. Run SETUP.bat to use the built-in AI engine      ║")
        print("  ║     (no Ollama needed)                               ║")
        print("  ║  2. Install Ollama from ollama.com and try again     ║")
        print("  ╚══════════════════════════════════════════════════════╝")
        print()
        input("  Press Enter to exit...")
        sys.exit(1)


# ── Wait for the FastAPI server ──────────────────────────────────

def wait_for_server(timeout: int = 90) -> bool:  # 90s for USB model loading
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://localhost:{PORT}/api/status", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ── Main ─────────────────────────────────────────────────────────

def main():
    print()
    print("  [*]  PocketAI — starting up...")

    check_ollama()

    # Start the uvicorn server
    venv_python = HERE / "venv" / "Scripts" / "python.exe"
    python_exe  = str(venv_python) if venv_python.exists() else sys.executable

    proc = subprocess.Popen(
        [
            python_exe, "-m", "uvicorn",
            "server.main:app",
            "--host",      "0.0.0.0",
            "--port",      str(PORT),
            "--log-level", "warning",
        ],
        cwd=str(HERE),
    )

    print(f"  [*]  Server starting on http://localhost:{PORT}")

    if wait_for_server():
        print(f"  [*]  PocketAI ready! Opening browser...")
        webbrowser.open(f"http://localhost:{PORT}")
    else:
        print("  WARNING  Server did not start in time — please open your browser manually:")
        print(f"     http://localhost:{PORT}")

    print("  [*]  Press Ctrl+C to stop PocketAI\n")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  [*]  Shutting down PocketAI...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("  [*]  Goodbye!\n")


if __name__ == "__main__":
    main()
