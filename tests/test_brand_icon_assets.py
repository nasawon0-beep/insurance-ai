from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "assets" / "brand"
WINDOWS_ICONS = ROOT / "desktop" / "src-tauri" / "icons"
ANDROID_RES = ROOT / "android" / "app" / "src" / "main" / "res"
EXPECTED_PNG_SHA256 = "76183f4ec8ae96bde662a6eed9c7e6e31b8b8eba1584d0c159f0442a54164894"
EXPECTED_SVG_SHA256 = "f0b103e3d9a2a9000db1a5ff3f6e2f00f43d2b3967507307e80ec1010fdec893"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selected_round2_b_source_is_preserved_exactly() -> None:
    assert sha256(SOURCE_DIR / "moadoom-icon-b.png") == EXPECTED_PNG_SHA256
    assert sha256(SOURCE_DIR / "moadoom-icon-b.svg") == EXPECTED_SVG_SHA256


def test_windows_tauri_icons_are_generated_from_selected_source() -> None:
    expected_sizes = {
        "32x32.png": (32, 32),
        "128x128.png": (128, 128),
        "128x128@2x.png": (256, 256),
        "icon.png": (512, 512),
        "StoreLogo.png": (50, 50),
        "Square44x44Logo.png": (44, 44),
        "Square150x150Logo.png": (150, 150),
        "Square310x310Logo.png": (310, 310),
    }
    for name, size in expected_sizes.items():
        with Image.open(WINDOWS_ICONS / name) as image:
            assert image.size == size
            assert image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0
    with Image.open(WINDOWS_ICONS / "icon.ico") as image:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= image.ico.sizes()


def test_android_adaptive_and_themed_icons_reference_b_resources() -> None:
    colors = (ANDROID_RES / "values" / "colors.xml").read_text(encoding="utf-8")
    assert '<color name="ic_launcher_background">#285AA8</color>' in colors

    foreground = (ANDROID_RES / "drawable" / "ic_launcher_foreground.xml").read_text(encoding="utf-8")
    assert "#F7F0DF" in foreground
    assert "#E6D8BD" in foreground
    assert "gradient" not in foreground.lower()

    monochrome = ANDROID_RES / "drawable" / "ic_launcher_monochrome.xml"
    ET.parse(monochrome)
    assert "#000000" in monochrome.read_text(encoding="utf-8")

    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        path = ANDROID_RES / "mipmap-anydpi-v26" / name
        ET.parse(path)
        text = path.read_text(encoding="utf-8")
        assert '@drawable/ic_launcher_foreground' in text
        assert '@drawable/ic_launcher_monochrome' in text
        assert '@color/ic_launcher_background' in text


def test_tauri_installer_uses_b_icon_without_changing_product_identity() -> None:
    config = json.loads((ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["productName"] == "Insurance AI"
    assert config["mainBinaryName"] == "Insurance AI"
    assert config["identifier"] == "com.abc.insurance-ai"
    assert config["bundle"]["windows"]["nsis"]["installerIcon"] == "icons/icon.ico"
    assert config["bundle"]["windows"]["nsis"]["uninstallerIcon"] == "icons/icon.ico"
    manifest = (ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:label="모아둠"' in manifest
    assert 'android:icon="@mipmap/ic_launcher"' in manifest
    assert 'android:roundIcon="@mipmap/ic_launcher_round"' in manifest
