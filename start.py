"""
start.py — PocketAI launcher.

1. Checks Ollama is installed and running.
2. Ensures both AI models are pulled locally.
3. Configures Ollama for dual-model parallel operation.
4. Starts the FastAPI server.
5. Opens the browser.
"""

import os
import sys
import subprocess
import time
import webbrowser
import threading
import shutil
import json
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── Detect hardware tier for model selection ──────────────────
from server.config import detect_config, SERVER_PORT, DATA_DIR, OLLAMA_HOST

cfg = detect_config()
print(f"\n  PocketAI  —  {cfg.description}")
print(f"  Models:  {cfg.model_a}  +  {cfg.model_b}")
print(f"  Data:    {DATA_DIR}\n")

# ── Ensure data directory exists ──────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)

# ── Set Ollama env vars for max performance ───────────────────
os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "2")   # keep both in RAM
os.environ.setdefault("OLLAMA_NUM_PARALLEL",      "2")   # serve both simultaneously
os.environ.setdefault("OLLAMA_FLASH_ATTENTION",   "1")   # faster attention on GPU

# ── Check / start Ollama ──────────────────────────────────────
def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _find_ollama() -> str:
    """
    Find the Ollama binary.
    Search order:
      1. USB engines/ folder (zero-install USB mode)
      2. System PATH (shutil.which)
      3. Windows default install location (not always on PATH)
    """
    local = os.path.join(HERE, "engines", "ollama.exe")
    if os.path.exists(local):
        return local
    found = shutil.which("ollama")
    if found:
        return found
    # Ollama on Windows installs here but doesn't always add itself to PATH
    win_default = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"
    )
    if os.path.exists(win_default):
        return win_default
    return None


def start_ollama():
    ollama_path = _find_ollama()
    if not ollama_path:
        print("  ERROR: Ollama is not available.")
        print("  For USB use: place ollama.exe in the engines/ folder.")
        print("  For PC use: download from https://ollama.com/download")
        sys.exit(1)

    print("  Starting Ollama service…", end="", flush=True)
    subprocess.Popen(
        [ollama_path, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    for _ in range(20):
        time.sleep(1)
        print(".", end="", flush=True)
        if ollama_running():
            print(" ready.")
            return
    print("\n  ERROR: Ollama did not start in time. Check your installation.")
    sys.exit(1)


if not ollama_running():
    start_ollama()
else:
    print("  Ollama: already running")


# ── Pull models if not present ────────────────────────────────
def model_present(name: str) -> bool:
    try:
        req  = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        return any(m["name"].startswith(name.split(":")[0]) for m in data.get("models", []))
    except Exception:
        return False


def pull_model(name: str):
    ollama_path = shutil.which("ollama")
    print(f"  Downloading {name} — this may take a few minutes…")
    result = subprocess.run([ollama_path, "pull", name])
    if result.returncode != 0:
        print(f"  WARNING: Could not pull {name}. The other model will be used alone.")


# In single-model mode (8 GB RAM) only pull model_a to avoid exhausting RAM
models_to_check = [cfg.model_a] if cfg.single_model else [cfg.model_a, cfg.model_b]
for model in models_to_check:
    if not model:
        continue
    if model_present(model):
        print(f"  Model ready:  {model}")
    else:
        pull_model(model)


# ── Install Python dependencies ───────────────────────────────
venv_python = os.path.join(HERE, "venv", "Scripts", "python.exe")
if not os.path.exists(venv_python):
    venv_python = sys.executable

req_file = os.path.join(HERE, "requirements.txt")
print("  Checking Python dependencies…")
subprocess.run(
    [venv_python, "-m", "pip", "install", "-r", req_file, "-q"],
    check=False
)


# ── Launch FastAPI server ─────────────────────────────────────
def open_browser():
    time.sleep(2.5)
    url = f"http://localhost:{SERVER_PORT}"
    print(f"\n  ✓  Opening PocketAI at {url}\n")
    webbrowser.open(url)

threading.Thread(target=open_browser, daemon=True).start()

print(f"  Starting server on port {SERVER_PORT}…")
print("  Press Ctrl+C to stop.\n")

subprocess.run([
    venv_python, "-m", "uvicorn",
    "server.main:app",
    "--host", "0.0.0.0",
    "--port", str(SERVER_PORT),
    "--workers", "1",
    "--log-level", "warning",
], cwd=HERE)
