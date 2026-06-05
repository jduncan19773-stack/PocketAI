
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(r'C:\Users\jdunc\Documents\PYTONPROJECTS\PocketAI')

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
    hooksconfig={},
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
