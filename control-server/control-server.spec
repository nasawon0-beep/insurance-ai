# -*- mode: python ; coding: utf-8 -*-

import sys
import importlib.util

# PyInstaller warnings alone must not permit a non-starting sidecar.
for required_module in ("fastapi", "uvicorn", "jwt", "bcrypt"):
    if importlib.util.find_spec(required_module) is None:
        raise RuntimeError(f"CONTROL_DEPENDENCY_MISSING:{required_module}")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("update_manifest.json", ".")],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="control-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "win32",
)
