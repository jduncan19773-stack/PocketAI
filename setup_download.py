"""
setup_download.py — First-time USB setup.

Downloads:
  1. llama-server.exe  (llama.cpp release, Windows x64, AVX2 build)
  2. phi4-mini-q4_k_m.gguf   (~2.3 GB) — primary model
  3. qwen3-4b-q4_k_m.gguf    (~2.5 GB) — secondary model (optional, 16 GB+ machines)

All files land in the correct USB subdirectories (bin/ and models/).
Run with:  python setup_download.py
           python setup_download.py --no-secondary   (skip qwen3, 8 GB machines)

Requires: Python 3.10+, internet connection (one-time only).
After this script, PocketAI runs 100% offline.
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

HERE      = Path(__file__).parent
BIN_DIR   = HERE / "bin"
MODEL_DIR = HERE / "models"

BIN_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

# ── Download targets ─────────────────────────────────────────────

# llama.cpp latest Windows AVX2 release (update tag when a newer release ships)
LLAMA_RELEASE_TAG = "b5011"
LLAMA_ZIP_URL = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_RELEASE_TAG}/llama-{LLAMA_RELEASE_TAG}-bin-win-avx2-x64.zip"
)
LLAMA_EXE_IN_ZIP = "llama-server.exe"
LLAMA_EXE_DEST   = BIN_DIR / "llama-server.exe"

# Phi-4-mini Q4_K_M — ~2.3 GB
PHI4_URL  = (
    "https://huggingface.co/microsoft/Phi-4-mini-instruct-GGUF/resolve/main/"
    "Phi-4-mini-instruct-Q4_K_M.gguf"
)
PHI4_DEST = MODEL_DIR / "phi4-mini-q4_k_m.gguf"

# Qwen3-4B Q4_K_M — ~2.5 GB
QWEN3_URL  = (
    "https://huggingface.co/bartowski/Qwen3-4B-GGUF/resolve/main/"
    "Qwen3-4B-Q4_K_M.gguf"
)
QWEN3_DEST = MODEL_DIR / "qwen3-4b-q4_k_m.gguf"


# ── Download helper ───────────────────────────────────────────────

def download(url: str, dest: Path, label: str) -> bool:
    """Stream-download url to dest with a progress bar. Returns True on success."""
    if dest.exists():
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  ✓  {label} already downloaded ({size_mb:.0f} MB) — skipping")
        return True

    print(f"\n  ↓  {label}")
    print(f"     {url}")
    tmp = dest.with_suffix(".part")
    try:
        req = Request(url, headers={"User-Agent": "PocketAI-Setup/1.0"})
        with urlopen(req, timeout=60) as resp:
            total   = int(resp.headers.get("Content-Length", 0))
            written = 0
            chunk   = 1 << 20   # 1 MB
            with open(tmp, "wb") as fh:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    written += len(buf)
                    if total:
                        pct = written * 100 // total
                        mb  = written / 1_048_576
                        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                        print(f"\r     [{bar}] {pct}% — {mb:.0f} MB", end="", flush=True)
        tmp.rename(dest)
        print(f"\r     ✓  {dest.name} ({written / 1_048_576:.0f} MB)          ")
        return True
    except URLError as e:
        print(f"\n     ✗  Download failed: {e}")
        if tmp.exists():
            tmp.unlink()
        return False
    except KeyboardInterrupt:
        print("\n     Cancelled.")
        if tmp.exists():
            tmp.unlink()
        sys.exit(1)


def extract_llama_server(zip_path: Path) -> bool:
    """Extract llama-server.exe from the llama.cpp release zip."""
    print(f"  ↗  Extracting llama-server.exe…")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith(LLAMA_EXE_IN_ZIP):
                    data = zf.read(name)
                    LLAMA_EXE_DEST.write_bytes(data)
                    print(f"     ✓  {LLAMA_EXE_DEST}")
                    return True
        print(f"     ✗  {LLAMA_EXE_IN_ZIP} not found in zip")
        return False
    except Exception as e:
        print(f"     ✗  Extraction failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-secondary", action="store_true",
                    help="Skip qwen3-4b download (recommended for 8 GB machines)")
    args = ap.parse_args()

    print()
    print("  ◈  PocketAI — First-Time USB Setup")
    print("  ─────────────────────────────────────────────────────")
    print("  This downloads the AI engine and models (~5-6 GB).")
    print("  After this, PocketAI works 100% offline forever.")
    print()

    errors = []

    # 1. llama-server.exe
    if not LLAMA_EXE_DEST.exists():
        zip_dest = BIN_DIR / "llama.zip"
        ok = download(LLAMA_ZIP_URL, zip_dest, "llama-server engine (Windows x64 AVX2)")
        if ok:
            if extract_llama_server(zip_dest):
                zip_dest.unlink()   # remove zip after extraction
            else:
                errors.append("llama-server.exe")
        else:
            errors.append("llama-server engine")
    else:
        print(f"  ✓  llama-server.exe already present")

    # 2. phi4-mini
    if not download(PHI4_URL, PHI4_DEST, "Phi-4-mini model (~2.3 GB)"):
        errors.append("phi4-mini model")

    # 3. qwen3-4b (optional)
    if not args.no_secondary:
        if not download(QWEN3_URL, QWEN3_DEST, "Qwen3-4B model (~2.5 GB, optional)"):
            print("     (Skipping qwen3-4b — dual-model will be disabled)")
    else:
        print("  –  Skipping qwen3-4b (--no-secondary)")

    # Summary
    print()
    print("  ─────────────────────────────────────────────────────")
    if errors:
        print(f"  ⚠  Some downloads failed: {', '.join(errors)}")
        print("     Check your internet connection and try again.")
    else:
        print("  ✓  Setup complete!")
        print()
        print("  To start PocketAI:  double-click START_POCKETAI.bat")
        print("                 or:  python run.py --usb")
    print()


if __name__ == "__main__":
    main()
