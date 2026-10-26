# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import importlib.util

from PyInstaller.utils.hooks import collect_all


if sys.version_info[:2] != (3, 11):
    raise RuntimeError(
        "local-engine bundle must be built with Python 3.11; "
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
# 한국어 OCR 인식 모델은 항상 번들 (README: "번들 필수 — 다운로드 아님", 주로 Windows/Linux 용).
datas = [
    ("ocr/models", "ocr/models"),
    ("database/coverage-rag-queries.json", "database"),
    ("database/coverage-recommended-amounts.json", "database"),
    ("database/import_data.py", "database"),
]
# pdf-ocr 는 macOS Vision 헬퍼(Mach-O) — macOS 빌드에서, 존재할 때만.
if sys.platform == "darwin" and os.path.exists("ocr/pdf-ocr"):
    datas += [("ocr/pdf-ocr", "ocr")]
binaries = []

# cryptography 50 imports this top-level C extension dynamically.  It is not
# inside the cffi package directory, so collect_all("cffi") alone misses it.
cffi_backend = importlib.util.find_spec("_cffi_backend")
if cffi_backend is None or not cffi_backend.origin:
    raise RuntimeError("_cffi_backend is not importable in the Python 3.11 build environment")
binaries += [(cffi_backend.origin, ".")]

# pydantic_core 바이너리 명시 (Windows)
if sys.platform == "win32":
    import site
    site_packages = site.getsitepackages()[0]
    pydantic_core_pyd = os.path.join(site_packages, "pydantic_core", "_pydantic_core.cp311-win_amd64.pyd")
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

a = Analysis(
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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="local-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
