"""
launcher.py — llama-server.exe process manager for USB (offline) mode.

Only used when POCKETAI_MODE=usb is set.

Each model in the registry has its own port. On startup we launch only the
default model so the app is usable immediately. Other models are started
on demand the first time they are selected (ensure_model), which keeps RAM
usage low on 8 GB machines — you only pay for the models you actually use.

Ollama mode (default): nothing here runs; the app talks to Ollama directly.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx

from server.config import AVAILABLE_MODELS

_HERE = Path(__file__).parent.parent   # USB root (project root)

BIN_DIR    = _HERE / "bin"
MODELS_DIR = _HERE / "models"
LLAMA_EXE  = BIN_DIR / "llama-server.exe"

# The model started immediately on launch (fast first-use)
DEFAULT_MODEL = "phi4-mini"

HEALTH_TIMEOUT = 90    # seconds to wait for a model server to become ready
CONTEXT_SIZE   = 16384 # 16K tokens for long multi-turn conversations

# Legacy aliases kept so older code/tests referencing these still import
MODEL_A_GGUF = MODELS_DIR / AVAILABLE_MODELS["phi4-mini"]["gguf"]
MODEL_B_GGUF = MODELS_DIR / AVAILABLE_MODELS["qwen3-4b"]["gguf"]


class LlamaLauncher:
    """Starts and stops llama-server.exe instances, one per model, on demand."""

    def __init__(self):
        # model_id -> subprocess.Popen
        self._procs: dict[str, subprocess.Popen] = {}
        # Guards concurrent ensure_model calls for the same model
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Startup / shutdown ───────────────────────────────────────

    async def start(self, cfg) -> None:
        """Start the default model so the app is immediately usable."""
        if not LLAMA_EXE.exists():
            print(f"[launcher] llama-server.exe missing at {LLAMA_EXE}. Run SETUP.bat.")
            return
        await self.ensure_model(DEFAULT_MODEL)

    async def stop(self) -> None:
        """Terminate all running llama-server processes."""
        for proc in self._procs.values():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()

    # ── On-demand model loading ──────────────────────────────────

    async def ensure_model(self, model_id: str) -> bool:
        """
        Make sure the llama-server for `model_id` is running and ready.
        Returns True if the model is available to serve requests.
        Safe to call repeatedly — it is a no-op if already running.
        """
        if model_id not in AVAILABLE_MODELS:
            return False

        gguf = MODELS_DIR / AVAILABLE_MODELS[model_id]["gguf"]
        port = AVAILABLE_MODELS[model_id]["port"]
        if not gguf.exists():
            print(f"[launcher] GGUF not found for {model_id}: {gguf}")
            return False

        lock = self._locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            # Already running and healthy?
            proc = self._procs.get(model_id)
            if proc and proc.poll() is None:
                if await self._is_ready(port):
                    return True

            # (Re)start it
            print(f"[launcher] Starting {model_id} on port {port}")
            self._procs[model_id] = self._spawn(gguf, port)
            ok = await self._wait_ready(port, model_id)
            if not ok:
                print(f"[launcher] WARNING: {model_id} did not become ready")
            return ok

    async def ensure_models(self, model_ids: list) -> list:
        """Ensure several models are ready. Returns the ones that came up."""
        ready = []
        for mid in model_ids:
            if await self.ensure_model(mid):
                ready.append(mid)
        return ready

    # ── Internals ────────────────────────────────────────────────

    def _spawn(self, gguf: Path, port: int) -> subprocess.Popen:
        cmd = [
            str(LLAMA_EXE),
            "--model",    str(gguf),
            "--port",     str(port),
            "--host",     "127.0.0.1",
            "--ctx-size", str(CONTEXT_SIZE),
            "--threads",  str(max(2, (os.cpu_count() or 4) - 1)),
            # Memory-map the model (default): the server becomes ready quickly
            # and pages are loaded on demand, then cached in RAM. Forcing a full
            # read (--no-mmap) made startup read the whole 2.5GB from USB first,
            # which blocked launch past the timeout.
            "--log-disable",
        ]
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    async def _is_ready(self, port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"http://localhost:{port}/health")
                return r.status_code == 200
        except Exception:
            return False

    async def _wait_ready(self, port: int, label: str, timeout: int = HEALTH_TIMEOUT) -> bool:
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.time() < deadline:
                try:
                    r = await client.get(f"http://localhost:{port}/health")
                    if r.status_code == 200:
                        print(f"[launcher] {label} ready on port {port}")
                        return True
                except Exception:
                    pass
                await asyncio.sleep(1.5)
        return False


# ── Module-level singleton accessor ──────────────────────────────
# main.py creates the launcher; inference/main can reach it via this getter.

_active_launcher: "LlamaLauncher | None" = None

def set_active_launcher(launcher) -> None:
    global _active_launcher
    _active_launcher = launcher

def get_active_launcher():
    return _active_launcher
