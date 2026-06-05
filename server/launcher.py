"""
launcher.py — llama-server.exe process manager for USB (offline) mode.

Only used when POCKETAI_MODE=usb is set.

In USB mode, llama-server.exe lives at <USB_ROOT>/bin/llama-server.exe and
model GGUF files live at <USB_ROOT>/models/.

This module:
  1. Starts one llama-server instance per model (A on port 11434, B on 11435).
  2. Waits up to 60 s for each server to respond to a /health ping.
  3. On shutdown (or SIGTERM), kills both child processes cleanly.

Ollama mode (default, no POCKETAI_MODE=usb):
  Nothing here is called — the app talks directly to the already-running Ollama.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx

_HERE = Path(__file__).parent.parent   # USB root (project root)

# Paths on the USB stick
BIN_DIR    = _HERE / "bin"
MODELS_DIR = _HERE / "models"

LLAMA_EXE  = BIN_DIR / "llama-server.exe"

# Well-known GGUF filenames (downloaded by SETUP.bat)
MODEL_A_GGUF = MODELS_DIR / "phi4-mini-q4_k_m.gguf"
MODEL_B_GGUF = MODELS_DIR / "qwen3-4b-q4_k_m.gguf"

PORT_A = 11434
PORT_B = 11435

HEALTH_TIMEOUT = 60   # seconds to wait for a server to become ready
CONTEXT_SIZE   = 16384  # 16K tokens — supports long multi-turn conversations


class LlamaLauncher:
    """Starts and stops llama-server.exe instances for each model."""

    def __init__(self):
        self._procs: list[subprocess.Popen] = []

    async def start(self, cfg) -> None:
        """Start model servers based on the detected ModelConfig."""
        if not LLAMA_EXE.exists():
            print(f"[launcher] llama-server.exe not found at {LLAMA_EXE}. Run SETUP.bat first.")
            return

        # Always start model A
        if MODEL_A_GGUF.exists():
            proc_a = self._spawn(MODEL_A_GGUF, PORT_A, cfg.model_a)
            self._procs.append(proc_a)
            ok = await self._wait_ready(f"http://localhost:{PORT_A}", label=cfg.model_a)
            if not ok:
                print(f"[launcher] WARNING: {cfg.model_a} did not become ready in time")
        else:
            print(f"[launcher] Model A GGUF not found: {MODEL_A_GGUF}")

        # Start model B only in dual-model mode and if the file exists
        if not cfg.single_model and MODEL_B_GGUF.exists():
            proc_b = self._spawn(MODEL_B_GGUF, PORT_B, cfg.model_b)
            self._procs.append(proc_b)
            ok = await self._wait_ready(f"http://localhost:{PORT_B}", label=cfg.model_b)
            if not ok:
                print(f"[launcher] WARNING: {cfg.model_b} did not become ready in time")

    async def stop(self) -> None:
        """Terminate all llama-server processes."""
        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs.clear()

    def _spawn(self, gguf: Path, port: int, label: str) -> subprocess.Popen:
        """Launch a llama-server instance for the given model file."""
        cmd = [
            str(LLAMA_EXE),
            "--model",    str(gguf),
            "--port",     str(port),
            "--host",     "127.0.0.1",
            "--ctx-size", str(CONTEXT_SIZE),
            "--threads",  str(max(2, (os.cpu_count() or 4) - 1)),
            "--no-mmap",  # safer on USB (avoids memory-mapping the stick directly)
            "--log-disable",
        ]
        print(f"[launcher] Starting {label} on port {port}")
        # stdout/stderr go to devnull to keep the console clean for end-users
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    async def _wait_ready(self, base_url: str, label: str, timeout: int = HEALTH_TIMEOUT) -> bool:
        """Poll /health until the server responds 200 or timeout expires."""
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.time() < deadline:
                try:
                    r = await client.get(f"{base_url}/health")
                    if r.status_code == 200:
                        print(f"[launcher] {label} ready at {base_url}")
                        return True
                except Exception:
                    pass
                await asyncio.sleep(1.5)
        return False
