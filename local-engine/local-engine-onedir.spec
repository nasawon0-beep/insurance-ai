# -*- mode: python ; coding: utf-8 -*-

# Isolated Windows onedir candidate. Keep local-engine.spec as the onefile baseline.
import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_all


if sys.version_info[:2] != (3, 11):
    raise RuntimeError(
        "local-engine onedir bundle must be built with Python 3.11; "
        f"got {sys.version.split()[0]} from {sys.executable}"
    )


packages = [
    "lan_sync",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "cryptography",
    "cffi",
    "keyring",
    "pdfminer",
    "pdfplumber",
    "pypdfium2",
    "openpyxl",
    "numpy",
]
datas = [
    ("ocr/models", "ocr/models"),
    ("database/coverage-rag-queries.json", "database"),
    ("database/coverage-recommended-amounts.json", "database"),
    ("database/import_data.py", "database"),
]
if sys.platform == "darwin" and os.path.exists("ocr/pdf-ocr"):
    datas += [("ocr/pdf-ocr", "ocr")]
binaries = []

cffi_backend = importlib.util.find_spec("_cffi_backend")
if cffi_backend is None or not cffi_backend.origin:
    raise RuntimeError("_cffi_backend is not importable in the Python 3.11 build environment")
binaries += [(cffi_backend.origin, ".")]

if sys.platform == "win32":
    import site

    site_packages = site.getsitepackages()[0]
    pydantic_core_pyd = os.path.join(
        site_packages, "pydantic_core", "_pydantic_core.cp311-win_amd64.pyd"
    )
    if os.path.exists(pydantic_core_pyd):
        binaries += [(pydantic_core_pyd, "pydantic_core")]

hiddenimports = [
    "_cffi_backend",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "pydantic_core._pydantic_core",
]

for package in packages:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

analysis = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="local-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory="local-engine._internal",
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="local-engine-onedir",
)
