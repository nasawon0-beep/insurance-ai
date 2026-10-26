from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
HOOK = ROOT / "desktop" / "src-tauri" / "nsis" / "shortcut-name-hooks.nsh"
BRAND_ICON = ROOT / "desktop" / "src-tauri" / "icons" / "moadoom-brand-b.ico"
EXPECTED_TARGET = r"C:\Users\fixture\AppData\Local\Insurance AI\Insurance AI.exe"
EXPECTED_ICON = r"C:\Users\fixture\AppData\Local\Insurance AI\moadoom-brand-b.ico"


def migrate_fixture(old_target: str | None, new_target: str | None) -> tuple[str | None, str | None]:
    """Model the hook's target-gated rename/dedup policy with synthetic paths only."""
    if old_target != EXPECTED_TARGET:
        return old_target, new_target
    if new_target is None:
        return None, EXPECTED_TARGET
    if new_target == EXPECTED_TARGET:
        return None, EXPECTED_TARGET
    return old_target, new_target


def uninstall_fixture(new_target: str | None, *, update_mode: bool) -> str | None:
    if not update_mode and new_target == EXPECTED_TARGET:
        return None
    return new_target


def icon_fixture(target: str | None, current_icon: str | None) -> str | None:
    if target == EXPECTED_TARGET:
        return EXPECTED_ICON
    return current_icon


def test_nsis_config_uses_official_current_user_installer_hook() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    nsis = config["bundle"]["windows"]["nsis"]
    assert nsis["installMode"] == "currentUser"
    assert nsis["installerHooks"] == "nsis/shortcut-name-hooks.nsh"
    assert config["productName"] == "Insurance AI"
    assert config["mainBinaryName"] == "Insurance AI"
    assert config["identifier"] == "com.abc.insurance-ai"
    assert config["bundle"]["resources"] == {
        "icons/moadoom-brand-b.ico": "moadoom-brand-b.ico"
    }
    hook = HOOK.read_text(encoding="utf-8")
    assert f'!define MOADOOM_LEGACY_SHORTCUT_NAME "{config["productName"]}"' in hook
    assert f'!define MOADOOM_MAIN_BINARY_NAME "{config["mainBinaryName"]}"' in hook


def test_hook_is_target_gated_and_never_creates_shortcuts() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert "NSIS_HOOK_POSTINSTALL" in text
    assert "NSIS_HOOK_PREUNINSTALL" in text
    assert "Function .onGUIEnd" in text
    assert "!insertmacro IsShortcutTarget" in text
    assert '"$INSTDIR\\${MOADOOM_MAIN_BINARY_NAME}.exe"' in text
    assert '!define MOADOOM_BRAND_ICON_FILE "moadoom-brand-b.ico"' in text
    assert '"$INSTDIR\\${MOADOOM_BRAND_ICON_FILE}"' in text
    assert "${IShellLink::SetIconLocation}" in text
    assert "$UpdateMode != 1" in text
    assert "CreateShortcut" not in text
    assert "WriteReg" not in text
    assert "Exec" not in text
    assert "RMDir" not in text


def test_bundled_shortcut_icon_is_the_verified_b_asset() -> None:
    import hashlib

    assert hashlib.sha256(BRAND_ICON.read_bytes()).hexdigest() == (
        "77abcf6dba9b22bea5de1705537e4eafb2bcd01cd6b2e59950aaf7d2e170baf4"
    )


def test_icon_location_changes_only_for_exact_app_target() -> None:
    old_icon = r"C:\Other\old.ico"
    assert icon_fixture(EXPECTED_TARGET, old_icon) == EXPECTED_ICON
    assert icon_fixture(r"C:\Other\Other.exe", old_icon) == old_icon
    assert icon_fixture(None, None) is None


def test_update_renames_only_owned_old_link_and_deduplicates_owned_new_link() -> None:
    assert migrate_fixture(EXPECTED_TARGET, None) == (None, EXPECTED_TARGET)
    assert migrate_fixture(EXPECTED_TARGET, EXPECTED_TARGET) == (None, EXPECTED_TARGET)


def test_unknown_or_other_app_shortcuts_are_preserved() -> None:
    other = r"C:\Other\Other.exe"
    assert migrate_fixture(other, None) == (other, None)
    assert migrate_fixture(EXPECTED_TARGET, other) == (EXPECTED_TARGET, other)
    assert migrate_fixture(None, other) == (None, other)


def test_no_shortcut_state_stays_empty() -> None:
    assert migrate_fixture(None, None) == (None, None)


def test_uninstall_removes_only_owned_new_link_and_update_keeps_it() -> None:
    other = r"C:\Other\Other.exe"
    assert uninstall_fixture(EXPECTED_TARGET, update_mode=False) is None
    assert uninstall_fixture(EXPECTED_TARGET, update_mode=True) == EXPECTED_TARGET
    assert uninstall_fixture(other, update_mode=False) == other
