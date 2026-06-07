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


# ── Model registry ───────────────────────────────────────────────
# Every model PocketAI can use. Each entry:
#   id      — stable key used in the API and UI toggle
#   tag     — Ollama model tag (used in Ollama mode)
#   gguf    — GGUF filename in models/ (used in USB / llama-server mode)
#   label   — friendly name shown in the UI
#   blurb   — one-line description shown in the UI
#   vision  — True if it is an image model (excluded from the text "all" merge)
#   port    — llama-server port reserved for this model in USB mode

# USB-mode llama-server ports. Deliberately NOT 11434 (Ollama's default) so
# the bundled engine never collides with an Ollama install on the host.
AVAILABLE_MODELS = {
    "phi4-mini": {
        "tag":   "phi4-mini",
        "gguf":  "phi4-mini-q4_k_m.gguf",
        "label": "Phi-4 Mini",
        "blurb": "Fast & balanced — Microsoft",
        "vision": False,
        "port":  17431,
    },
    "qwen3-4b": {
        "tag":   "qwen3:4b",
        "gguf":  "qwen3-4b-q4_k_m.gguf",
        "label": "Qwen 3 (4B)",
        "blurb": "Strong reasoning — Alibaba",
        "vision": False,
        "port":  17432,
    },
    "llama3.2-3b": {
        "tag":   "llama3.2:3b",
        "gguf":  "llama3.2-3b-q4_k_m.gguf",
        "label": "Llama 3.2 (3B)",
        "blurb": "Versatile all-rounder — Meta",
        "vision": False,
        "port":  17433,
    },
    "moondream": {
        "tag":   "moondream",
        "gguf":  "moondream2-q4.gguf",
        "label": "Moondream 2",
        "blurb": "Understands images",
        "vision": True,
        "port":  17434,
    },
}

# Order shown in the UI toggle
MODEL_ORDER = ["phi4-mini", "qwen3-4b", "llama3.2-3b", "moondream"]

# Text models eligible for the "all models" merge (vision models excluded)
def text_model_ids() -> list:
    return [m for m in MODEL_ORDER if not AVAILABLE_MODELS[m]["vision"]]


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
    Detect hardware and return the best model configuration.

    Dual-model requires enough VRAM to hold both models simultaneously.
    Running dual-model on insufficient VRAM causes model thrashing (8x slower).

    Tiers:
      high   — >= 8 GB VRAM  AND >= 18 GB RAM free: phi4-mini + qwen3:8b
      medium — >= 6 GB VRAM  AND >= 8  GB RAM free: phi4-mini + qwen3:4b
      low    — everything else: phi4-mini only (single model, fast, reliable)
    """
    available_gb = psutil.virtual_memory().available / (1024 ** 3)
    gpu_vram_mb  = _get_gpu_vram_mb()
    gpu_note     = f", {gpu_vram_mb} MB VRAM" if gpu_vram_mb else ""

    # Dual-model only when there's enough VRAM to GPU-accelerate both
    if gpu_vram_mb >= 8192 and available_gb >= 18:
        return ModelConfig(
            model_a="phi4-mini",
            model_b="qwen3:8b",
            tier="high",
            single_model=False,
            description=f"High — {available_gb:.0f} GB RAM free{gpu_note}",
        )
    elif gpu_vram_mb >= 6144 and available_gb >= 8:
        return ModelConfig(
            model_a="phi4-mini",
            model_b="qwen3:4b",
            tier="medium",
            single_model=False,
            description=f"Dual-model — {available_gb:.0f} GB RAM free{gpu_note}",
        )
    else:
        # 4 GB VRAM / 8 GB RAM i3 target: phi4-mini only for fast, reliable answers
        return ModelConfig(
            model_a="phi4-mini",
            model_b="",
            tier="low",
            single_model=True,
            description=f"Fast single-model — {available_gb:.0f} GB RAM free{gpu_note}",
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
    "You are PocketAI, a private AI assistant running entirely on the user's device — "
    "no internet, no cloud, completely private. "
    "Lead with the answer — skip filler openers like 'Certainly', 'Of course', "
    "'Great question', 'I understand', or repeating the question back. "
    "Match the depth of your answer to the request: when the user asks you to "
    "explain, show your reasoning, give steps, or go into detail, do exactly that "
    "and walk through your thinking; when they ask something simple, keep it short. "
    "Be accurate and honest — if you're uncertain, say so. Use markdown when it helps."
)


# ── Runtime flags ────────────────────────────────────────────────

# Set POCKETAI_MODE=usb to enable the built-in llama-server launcher
USB_MODE = os.environ.get("POCKETAI_MODE", "").lower() == "usb"

# Zero-footprint: suppress .pyc writes to the host machine
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
