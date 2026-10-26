import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

from lan_sync.certificate import ensure_certificate
from lan_sync.store import SyncStore


def test_address_change_preserves_existing_certificate_and_requires_confirmation(tmp_path):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    reused = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_before = Path(first["cert_path"]).read_bytes()
    key_before = Path(first["key_path"]).read_bytes()

    with pytest.raises(ValueError, match="address changed.*confirmation required"):
        ensure_certificate(tmp_path, ["192.168.50.11"], valid_days=30, renew_before_days=5)

    assert first["created"] is True
    assert reused["created"] is False
    assert reused["fingerprint"] == first["fingerprint"]
    assert Path(first["cert_path"]).read_bytes() == cert_before
    assert Path(first["key_path"]).read_bytes() == key_before


def test_certificate_key_mismatch_fails_closed_without_replacing_identity(tmp_path):
    issued = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(issued["cert_path"])
    key_path = Path(issued["key_path"])
    replacement_key = ec.generate_private_key(ec.SECP256R1())
    key_path.write_bytes(
        replacement_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    mismatched_cert = cert_path.read_bytes()
    mismatched_key = key_path.read_bytes()

    with pytest.raises(ValueError, match="public key does not match"):
        ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    assert cert_path.read_bytes() == mismatched_cert
    assert key_path.read_bytes() == mismatched_key


def test_device_listing_never_returns_token_digest(tmp_path):
    store = SyncStore(tmp_path / "personal.sqlite3")
    store.initialize()
    try:
        code = store.create_pairing_code("개인 PC", 60)
        paired = store.pair_device(code.code, "내 휴대폰", "192.0.2.10")
        active = store.list_devices()
        assert active == [{
            "id": paired.device_id,
            "name": "내 휴대폰",
            "created_at": active[0]["created_at"],
            "revoked_at": None,
        }]
        assert "token" not in json.dumps(active).lower()
        store.revoke_device(paired.device_id)
        assert store.list_devices()[0]["revoked_at"] is not None
    finally:
        store.close()


def test_packaged_local_engine_contains_lan_sync_entry_modes():
    main = (ROOT / "local-engine" / "main.py").read_text(encoding="utf-8")
    spec = (ROOT / "local-engine" / "local-engine.spec").read_text(encoding="utf-8")

    assert '"lan_sync"' in spec
    assert '"--lan-sync-server"' in main
    assert '"--lan-sync-admin"' in main
    assert "from lan_sync.server import main as command_main" in main
    assert "from lan_sync.admin import main as command_main" in main


def test_android_release_security_defaults_are_present():
    manifest = (ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    activity = (
        ROOT
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "insuranceai"
        / "android"
        / "MainActivity.kt"
    ).read_text(encoding="utf-8")

    assert 'android:allowBackup="false"' in manifest
    assert 'android:usesCleartextTraffic="false"' in manifest
    assert "shouldProtectScreen(BuildConfig.DEBUG)" in activity
    assert "fun shouldProtectScreen(isDebug: Boolean): Boolean = !isDebug" in activity


def test_packaged_admin_contains_no_demo_seed_command_or_fixture():
    admin = (ROOT / "local-engine" / "lan_sync" / "admin.py").read_text(encoding="utf-8")

    assert "seed-demo" not in admin
    assert "demo-customer" not in admin
    assert "합성 고객" not in admin


def test_mobile_ui_labels_pairing_code_without_exposing_bearer_credentials():
    panel = (ROOT / "desktop" / "src" / "components" / "MobileConnectionPanel.tsx").read_text(encoding="utf-8")

    assert "1회 연결 코드" in panel
    assert "기기 인증정보는 화면에 표시하지 않습니다" in panel
    assert "bearer" not in panel.lower()


def test_pc_ui_separates_revoked_device_recovery_from_first_pairing():
    panel = (ROOT / "desktop" / "src" / "components" / "MobileConnectionPanel.tsx").read_text(encoding="utf-8")

    assert "연결 해제된 모바일" in panel
    assert "이 기기 다시 연결" in panel
    assert "기존 기기와 미전송 자료를 유지" in panel
    assert "mobile_sync_create_recovery_code" in panel
    assert "token" not in panel.lower()
    assert "uuid" not in panel.lower()