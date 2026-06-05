"""
config.py — Hardware detection and model tier selection.

Detects available RAM, picks the best model configuration, and exposes
paths and host URLs used by the rest of the server.

Tier logic (based on free RAM at startup):
  high    (≥ 18 GB free) : phi4-mini + qwen3:8b  — both run simultaneously
  medium  (≥  8 GB free) : phi4-mini + qwen3:4b  — dual model, fits 16 GB machine
  low     (<  8 GB free) : phi4-mini only         — i3 / 8 GB target, single model

In single_model mode only model_a is loaded/used; model_b is ignored entirely.
"""

import os
import subprocess
import psutil
from dataclasses import dataclass


@dataclass
class ModelConfig:
    model_a:      str   # Primary model name (Ollama tag or any name for llama-server)
    model_b:      str   # Secondary model name; empty string when single_model=True
    tier:         str   # "high" / "medium" / "low"
    description:  str   # Human-readable summary shown in /api/status
    single_model: bool  # True = only run model_a (conserves RAM on 8 GB machines)


def _get_gpu_vram_mb() -> int:
    """Return total NVIDIA GPU VRAM in MB, or 0 if no NVIDIA GPU detected."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0]
            return int(line) if line.isdigit() else 0
    except Exception:
        pass
    return 0


def detect_config() -> ModelConfig:
    """
    Detect available RAM and return the appropriate model configuration.
    Called on every request so it reflects current system state.
    """
    available_gb = psutil.virtual_memory().available / (1024 ** 3)
    gpu_vram_mb  = _get_gpu_vram_mb()
    gpu_note     = f", {gpu_vram_mb} MB VRAM" if gpu_vram_mb else ""

    if available_gb >= 18:
        return ModelConfig(
            model_a="phi4-mini",
            model_b="qwen3:8b",
            tier="high",
            single_model=False,
            description=f"High — {available_gb:.0f} GB RAM free{gpu_note}",
        )
    elif available_gb >= 8:
        return ModelConfig(
            model_a="phi4-mini",
            model_b="qwen3:4b",
            tier="medium",
            single_model=False,
            description=f"Dual-model — {available_gb:.0f} GB RAM free{gpu_note}",
        )
    else:
        # 8 GB i3 target: run phi4-mini only to avoid swapping
        return ModelConfig(
            model_a="phi4-mini",
            model_b="",
            tier="low",
            single_model=True,
            description=f"Single-model — {available_gb:.0f} GB RAM free (8 GB mode)",
        )


# ── File paths ───────────────────────────────────────────────────
# All data lives in DATA_DIR so it stays on the USB stick.

_HERE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.environ.get("POCKETAI_DATA",   os.path.join(_HERE, "data"))
MODELS_DIR  = os.environ.get("POCKETAI_MODELS", os.path.join(_HERE, "models"))
CACHE_DB    = os.path.join(DATA_DIR, "cache.db")
SESSIONS_DB = os.path.join(DATA_DIR, "sessions.db")

os.makedirs(DATA_DIR,   exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)


# ── LLM API hosts ────────────────────────────────────────────────
# LLM_HOST_A: primary model server (Ollama or llama-server instance A)
# LLM_HOST_B: secondary model server (same as A in Ollama mode)
#
# In Ollama mode both point to the same server; model names differentiate them.
# In USB / llama-server mode: A = port 11434, B = port 11435 (separate processes).

LLM_HOST_A = os.environ.get("LLM_HOST_A", "http://localhost:11434")
LLM_HOST_B = os.environ.get("LLM_HOST_B", "http://localhost:11434")  # same in Ollama mode

SERVER_PORT = int(os.environ.get("POCKETAI_PORT", "7860"))

# Legacy alias used by any code that imported OLLAMA_HOST directly
OLLAMA_HOST = LLM_HOST_A


# ── Base system prompt ───────────────────────────────────────────

BASE_SYSTEM_PROMPT = (
    "You are PocketAI, a helpful, honest, and concise AI assistant that runs entirely "
    "on the user's own device — no internet connection, no cloud, completely private. "
    "Give accurate, factual answers. If you are uncertain, say so clearly. "
    "Keep responses clear and well-structured. Avoid padding or repetition. "
    "When the user asks you to remember something, confirm you have noted it."
)


# ── Runtime flags ────────────────────────────────────────────────

# Set POCKETAI_MODE=usb to enable the built-in llama-server launcher
USB_MODE = os.environ.get("POCKETAI_MODE", "").lower() == "usb"

# Zero-footprint: suppress .pyc writes to the host machine
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
