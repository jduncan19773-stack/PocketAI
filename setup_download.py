"""
setup_download.py — First-time USB setup.

Downloads everything PocketAI needs to run 100% offline on ANY Windows PC,
even one with no Python, no Ollama, no nothing installed.

Downloads:
  1. Python 3.12 Embeddable (~10 MB) — so Python is never needed on the host
  2. llama-server.exe (~50 MB) — the AI engine, no Ollama required
  3. phi4-mini-q4_k_m.gguf (~2.3 GB) — primary AI model
  4. qwen3-4b-q4_k_m.gguf (~2.5 GB) — second model (optional, skip on 8 GB machines)

After this runs once:
  - PocketAI works offline forever
  - Plug into any Windows PC, double-click START_POCKETAI.bat, done
  - No installation, no admin rights, no internet required

Run with:
  python setup_download.py              (full, includes qwen3-4b)
  python setup_download.py --minimal    (phi4-mini only, good for 8 GB machines)
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

HERE         = Path(__file__).parent
BIN_DIR      = HERE / "bin"
MODEL_DIR    = HERE / "models"
PYTHON_DIR   = HERE / "python-embed"

BIN_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)
PYTHON_DIR.mkdir(exist_ok=True)

# ── Download targets ─────────────────────────────────────────────

# Python 3.12.10 Embeddable Package for Windows x64
PYTHON_ZIP_URL  = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PYTHON_ZIP_DEST = BIN_DIR / "python-embed.zip"

# pip bootstrap script (for installing requirements into embedded Python)
GET_PIP_URL  = "https://bootstrap.pypa.io/get-pip.py"
GET_PIP_DEST = BIN_DIR / "get-pip.py"

# llama.cpp Windows release — auto-detects whether to grab CPU or CUDA build.
# CPU build works on every PC.  CUDA build is 3-5x faster on NVIDIA GPUs.
def _llama_zip_url() -> str:
    tag = "b9519"
    base = f"https://github.com/ggerganov/llama.cpp/releases/download/{tag}"
    # Try nvidia-smi to detect NVIDIA GPU
    import subprocess
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=3)
        if r.returncode == 0 and b"GPU" in r.stdout:
            print("  NVIDIA GPU detected — downloading CUDA-accelerated build")
            return f"{base}/llama-{tag}-bin-win-cuda-12.4-x64.zip"
    except Exception:
        pass
    return f"{base}/llama-{tag}-bin-win-cpu-x64.zip"

LLAMA_EXE_DEST = BIN_DIR / "llama-server.exe"

# Phi-4-mini Q4_K_M ~2.3 GB — public mirror via lmstudio-community
PHI4_URL  = (
    "https://huggingface.co/lmstudio-community/Phi-4-mini-instruct-GGUF/resolve/main/"
    "Phi-4-mini-instruct-Q4_K_M.gguf"
)
PHI4_DEST = MODEL_DIR / "phi4-mini-q4_k_m.gguf"

# Qwen3-4B Q4_K_M ~2.5 GB — public mirror via lmstudio-community
QWEN3_URL  = (
    "https://huggingface.co/lmstudio-community/Qwen3-4B-GGUF/resolve/main/"
    "Qwen3-4B-Q4_K_M.gguf"
)
QWEN3_DEST = MODEL_DIR / "qwen3-4b-q4_k_m.gguf"


# ── Download helper ───────────────────────────────────────────────

def download(url: str, dest: Path, label: str) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        print(f"  ok  {label} ({dest.stat().st_size // 1_048_576} MB) — already downloaded")
        return True

    print(f"\n  Downloading {label}...")
    print(f"  {url}")
    tmp = dest.with_suffix(".part")
    try:
        req = Request(url, headers={"User-Agent": "PocketAI-Setup/1.0"})
        with urlopen(req, timeout=60) as resp:
            total   = int(resp.headers.get("Content-Length", 0))
            written = 0
            chunk   = 1 << 20
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
                        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
                        print(f"\r  [{bar}] {pct}%  {mb:.0f} MB", end="", flush=True)
        tmp.rename(dest)
        print(f"\r  Done: {dest.name} ({written // 1_048_576} MB)          ")
        return True
    except URLError as e:
        print(f"\n  Failed: {e}")
        if tmp.exists():
            tmp.unlink()
        return False
    except KeyboardInterrupt:
        if tmp.exists():
            tmp.unlink()
        sys.exit(0)


def extract_zip(zip_path: Path, dest_dir: Path, label: str,
                target_file: str = None) -> bool:
    """
    Extract zip into dest_dir.
    If target_file given, extract only that filename (any path in zip).
    Returns True on success.
    """
    print(f"  Extracting {label}...")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if target_file:
                for name in zf.namelist():
                    if name.endswith(target_file):
                        data = zf.read(name)
                        out  = dest_dir / target_file
                        out.write_bytes(data)
                        print(f"  Extracted: {out}")
                        return True
                print(f"  {target_file} not found in zip")
                return False
            else:
                zf.extractall(dest_dir)
                print(f"  Extracted to {dest_dir}")
                return True
    except Exception as e:
        print(f"  Extraction failed: {e}")
        return False


def setup_embedded_python() -> bool:
    """
    Download and configure Python Embeddable so PocketAI runs without
    any Python installation on the host machine.
    """
    python_exe = PYTHON_DIR / "python.exe"
    if python_exe.exists():
        print(f"  ok  Python Embeddable already set up at {PYTHON_DIR}")
        return True

    # Download
    if not download(PYTHON_ZIP_URL, PYTHON_ZIP_DEST, "Python 3.12 Embeddable (~10 MB)"):
        return False

    # Extract
    if not extract_zip(PYTHON_ZIP_DEST, PYTHON_DIR, "Python 3.12"):
        return False
    PYTHON_ZIP_DEST.unlink(missing_ok=True)

    # Enable site-packages in the embeddable Python so pip-installed
    # packages are found at runtime.
    pth_file = next(PYTHON_DIR.glob("python3*._pth"), None)
    if pth_file:
        text = pth_file.read_text()
        if "#import site" in text:
            pth_file.write_text(text.replace("#import site", "import site"))
            print("  Enabled site-packages in embedded Python")

    # Bootstrap pip
    if not download(GET_PIP_URL, GET_PIP_DEST, "pip bootstrap"):
        return False

    print("  Installing pip into embedded Python...")
    ret = os.system(f'"{python_exe}" "{GET_PIP_DEST}" --quiet 2>&1')
    if ret != 0:
        print("  Warning: pip install had issues (may still work)")

    # Install PocketAI requirements
    req_file = HERE / "requirements.txt"
    if req_file.exists():
        print("  Installing PocketAI dependencies into embedded Python...")
        ret = os.system(
            f'"{python_exe}" -m pip install -r "{req_file}" --quiet 2>&1'
        )
        if ret != 0:
            print("  Warning: some dependencies may not have installed correctly")
        else:
            print("  Dependencies installed.")

    GET_PIP_DEST.unlink(missing_ok=True)
    return python_exe.exists()


# ── Main ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="PocketAI first-time USB setup")
    ap.add_argument("--minimal",     action="store_true",
                    help="Download phi4-mini only (8 GB machines)")
    ap.add_argument("--skip-python", action="store_true",
                    help="Skip Python Embeddable (if Python is already installed)")
    args = ap.parse_args()

    print()
    print("  ============================================================")
    print("   PocketAI — First-Time USB Setup")
    print("  ============================================================")
    print("  Downloading everything needed to run offline on any Windows PC.")
    print("  This is a one-time setup (~5 GB total). Coffee time!")
    print()

    errors = []

    # 1. Python Embeddable (so no Python needed on host)
    if not args.skip_python:
        if not setup_embedded_python():
            errors.append("Python Embeddable")
            print("  Warning: will fall back to system Python if available.")
    else:
        print("  Skipping Python Embeddable (--skip-python)")

    # 2. llama-server.exe
    if not LLAMA_EXE_DEST.exists():
        zip_dest = BIN_DIR / "llama.zip"
        if download(_llama_zip_url(), zip_dest, "llama-server AI engine (~50-200 MB)"):
            if extract_zip(zip_dest, BIN_DIR, "llama-server.exe", "llama-server.exe"):
                zip_dest.unlink(missing_ok=True)
            else:
                errors.append("llama-server.exe")
        else:
            errors.append("llama-server engine")
    else:
        print(f"  ok  llama-server.exe already present")

    # 3. Phi-4-mini model
    if not download(PHI4_URL, PHI4_DEST, "Phi-4-mini AI model (~2.3 GB)"):
        errors.append("phi4-mini model")

    # 4. Qwen3-4B model (optional)
    if not args.minimal:
        if not download(QWEN3_URL, QWEN3_DEST, "Qwen3-4B AI model (~2.5 GB, optional)"):
            print("  Skipping qwen3-4b — dual-model will be disabled on this device.")
    else:
        print("  Skipping qwen3-4b (--minimal mode)")

    # Result
    print()
    print("  ============================================================")
    if errors:
        print(f"  Some items failed: {', '.join(errors)}")
        print("  Check your internet connection and run setup again.")
    else:
        print("  Setup complete! Everything is ready to run offline.")
        print()
        print("  To start PocketAI:")
        print("  -> Double-click START_POCKETAI.bat")
    print("  ============================================================")
    print()


if __name__ == "__main__":
    main()
