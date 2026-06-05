"""
run.py — PocketAI launcher.

Starts the FastAPI server then opens the browser automatically.

Usage:
  python run.py            # Ollama mode — Ollama must be running
  python run.py --usb      # USB mode — uses bundled llama-server.exe

USB startup time note:
  Loading the AI model from a flash drive takes 60-120 seconds on first launch.
  The launcher waits up to 3 minutes and shows a countdown.
  After the first load the model stays in RAM so subsequent questions are fast.
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent   # absolute path to the USB drive root
PORT = int(os.environ.get("POCKETAI_PORT", "7860"))
URL  = f"http://localhost:{PORT}"

# ── CLI arguments ────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="PocketAI launcher")
parser.add_argument("--usb",  action="store_true", help="USB mode (use bundled llama-server)")
parser.add_argument("--port", type=int, default=PORT)
args, _ = parser.parse_known_args()

if args.usb:
    os.environ["POCKETAI_MODE"] = "usb"
PORT = args.port
URL  = f"http://localhost:{PORT}"


# ── Ollama helpers ───────────────────────────────────────────────

_OLLAMA_WIN = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"
)

def _ollama_running() -> bool:
    try:
        return httpx.get("http://localhost:11434/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _start_ollama():
    import shutil
    exe = shutil.which("ollama") or (_OLLAMA_WIN if os.path.exists(_OLLAMA_WIN) else None)
    if not exe:
        return
    print("  Starting Ollama service...")
    subprocess.Popen(
        [exe, "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    for _ in range(15):
        time.sleep(1)
        if _ollama_running():
            print("  Ollama ready.")
            return


def check_ollama():
    if os.environ.get("POCKETAI_MODE") == "usb":
        return   # llama-server handles inference — no Ollama needed
    if not _ollama_running():
        _start_ollama()
    if not _ollama_running():
        print()
        print("  +---------------------------------------------------------+")
        print("  |  Ollama could not be started.                           |")
        print("  |  Option 1: Run SETUP.bat (uses built-in AI engine)      |")
        print("  |  Option 2: Install Ollama from ollama.com               |")
        print("  +---------------------------------------------------------+")
        print()
        input("  Press Enter to exit...")
        sys.exit(1)


# ── Wait for server with visible countdown ───────────────────────

def wait_for_server(timeout: int = 180) -> bool:
    """
    Poll /api/status until the server responds.
    Shows a live countdown — USB model loading takes 60-120 seconds.
    """
    deadline = time.time() + timeout
    last_msg  = ""

    while time.time() < deadline:
        try:
            r = httpx.get(f"{URL}/api/status", timeout=2)
            if r.status_code == 200:
                print("\r  AI model loaded and ready!                    ")
                return True
        except Exception:
            pass

        remaining = int(deadline - time.time())
        msg = f"\r  Loading AI model... please wait  ({remaining}s)"
        if msg != last_msg:
            print(msg, end="", flush=True)
            last_msg = msg
        time.sleep(1)

    print()
    return False


# ── Main ─────────────────────────────────────────────────────────

def main():
    os.environ["PYTHONUTF8"] = "1"   # prevent Windows console encoding crashes
    print()
    print("  ============================================================")
    print("   PocketAI — Starting up")
    print("  ============================================================")
    print()

    check_ollama()

    # Use the embedded Python on the drive if present, otherwise system Python
    embed_python = HERE / "python-embed" / "python.exe"
    venv_python  = HERE / "venv" / "Scripts" / "python.exe"
    if embed_python.exists():
        python_exe = str(embed_python)
    elif venv_python.exists():
        python_exe = str(venv_python)
    else:
        python_exe = sys.executable

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

    usb_mode = os.environ.get("POCKETAI_MODE") == "usb"
    if usb_mode:
        print("  Mode: USB (built-in AI engine, no internet needed)")
        print("  First launch: loading AI model from the drive...")
        print("  This takes about 60-90 seconds. Subsequent questions are fast.")
    else:
        print("  Mode: Ollama")
    print()

    # Always print the URL — user can open it manually if the browser call fails
    print(f"  When ready, your browser will open automatically.")
    print(f"  If it doesn't open, go to:  {URL}")
    print()

    if wait_for_server():
        try:
            webbrowser.open(URL)
            print(f"  Browser opened to {URL}")
        except Exception:
            print(f"  Could not open browser automatically.")
            print(f"  Open this address in Chrome or Edge:  {URL}")
    else:
        print(f"  The AI model is taking longer than expected to load.")
        print(f"  Try opening your browser manually:  {URL}")
        print(f"  If the page loads, PocketAI is working.")

    print()
    print("  PocketAI is running. Press Ctrl+C in this window to stop.")
    print()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down PocketAI...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("  Stopped. Goodbye!\n")


if __name__ == "__main__":
    main()
