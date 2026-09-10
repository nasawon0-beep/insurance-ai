# -*- mode: python ; coding: utf-8 -*-

import os
import sys

from PyInstaller.utils.hooks import collect_all


packages = [
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "cryptography",
    "keyring",
    "pdfminer",
    "pdfplumber",
    "pypdfium2",
    "openpyxl",
    "numpy",
]
# 한국어 OCR 인식 모델은 항상 번들 (README: "번들 필수 — 다운로드 아님", 주로 Windows/Linux 용).
datas = [("ocr/models", "ocr/models")]
# pdf-ocr 는 macOS Vision 헬퍼(Mach-O) — macOS 빌드에서, 존재할 때만.
if sys.platform == "darwin" and os.path.exists("ocr/pdf-ocr"):
    datas += [("ocr/pdf-ocr", "ocr")]
binaries = []
hiddenimports = [
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
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
    console=sys.platform != "win32",
)
