"""
build_exe.py — Build PocketAI.exe using PyInstaller.

Run: python build_exe.py

Output: dist/PocketAI/PocketAI.exe  (plus supporting DLLs)
        Copy the entire dist/PocketAI/ folder to the USB root, or just
        copy PocketAI.exe alongside its _internal/ dir.

The exe bundles:
  - Python runtime
  - FastAPI, uvicorn, httpx, aiosqlite, psutil, pywebview, pypdf, python-docx
  - server/ Python package
  - ui/ static files (index.html, app.js, style.css, marked.min.js)

NOT bundled (stay separate on the USB):
  - models/*.gguf  (too large)
  - bin/llama-server.exe + DLLs
  - data/  (created at runtime)
"""

import subprocess
import sys
import shutil
from pathlib import Path

HERE = Path(__file__).parent

SPEC = HERE / "pocketai.spec"

SPEC_CONTENT = f"""
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(r'{HERE}')

a = Analysis(
    [str(HERE / 'pocketai_app.py')],
    pathex=[str(HERE)],
    binaries=[],
    datas=[
        (str(HERE / 'server'),  'server'),
        (str(HERE / 'ui'),      'ui'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'aiosqlite',
        'psutil',
        'httpx',
        'pypdf',
        'docx',
        'webview',
        'clr',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=['tkinter','matplotlib','numpy','pandas'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PocketAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window — pure GUI app
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PocketAI',
)
"""

def main():
    print()
    print("  Building PocketAI.exe...")
    print()

    # Write spec file
    SPEC.write_text(SPEC_CONTENT, encoding="utf-8")

    # Clean previous build
    for d in [HERE / "dist", HERE / "build"]:
        if d.exists():
            shutil.rmtree(d)

    # Run PyInstaller
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=str(HERE),
    )

    if result.returncode != 0:
        print("\n  Build FAILED. Check the output above.")
        sys.exit(1)

    exe = HERE / "dist" / "PocketAI" / "PocketAI.exe"
    if exe.exists():
        size_mb = sum(f.stat().st_size for f in (HERE / "dist" / "PocketAI").rglob("*") if f.is_file()) // 1_048_576
        print()
        print(f"  Build succeeded!  dist/PocketAI/  ({size_mb} MB)")
        print()
        print("  To use on the USB drive:")
        print("  1. Copy the entire dist/PocketAI/ folder to the USB root")
        print("  2. Also copy models/ and bin/ to the same USB root")
        print("  3. Double-click PocketAI.exe")
    else:
        print("\n  Build completed but PocketAI.exe not found.")


if __name__ == "__main__":
    main()
