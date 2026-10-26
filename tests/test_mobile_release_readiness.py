import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

import lan_sync.certificate as certificate_module
from lan_sync.certificate import ensure_certificate
from lan_sync.admin import tls_rollover_ready
from lan_sync.server import certificate_spki_sha256
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


def test_migrated_address_change_reissues_leaf_with_same_spki(tmp_path):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    rotated = ensure_certificate(
        tmp_path,
        ["192.168.50.11"],
        valid_days=30,
        renew_before_days=5,
        allow_address_reissue=True,
    )

    assert rotated["created"] is True
    assert rotated["reason"] == "address_changed"
    assert rotated["fingerprint"] != first["fingerprint"]
    assert rotated["spki_sha256"] == first["spki_sha256"]
    assert certificate_spki_sha256(Path(rotated["cert_path"])) == first["spki_sha256"]


def test_address_reissue_replace_failure_keeps_old_certificate_and_key(tmp_path, monkeypatch):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    key_path = Path(first["key_path"])
    cert_before = cert_path.read_bytes()
    key_before = key_path.read_bytes()
    original_replace = Path.replace

    def fail_certificate_replace(source, target):
        if source.name == "mobile-sync-cert.tmp":
            raise OSError("synthetic replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_certificate_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        ensure_certificate(
            tmp_path,
            ["192.168.50.11"],
            valid_days=30,
            renew_before_days=5,
            allow_address_reissue=True,
        )

    assert cert_path.read_bytes() == cert_before
    assert key_path.read_bytes() == key_before


def test_metadata_replace_failure_rolls_back_certificate_and_cleans_temps(tmp_path, monkeypatch):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    metadata_path = tmp_path / "mobile-sync-certificate.json"
    cert_before = cert_path.read_bytes()
    metadata_before = metadata_path.read_bytes()
    original_replace = Path.replace

    def fail_metadata_replace(source, target):
        if Path(target).name == "mobile-sync-certificate.json":
            raise OSError("synthetic metadata replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_metadata_replace)
    with pytest.raises(OSError, match="synthetic metadata replace failure"):
        ensure_certificate(
            tmp_path,
            ["192.168.50.11"],
            valid_days=30,
            renew_before_days=5,
            allow_address_reissue=True,
        )

    assert cert_path.read_bytes() == cert_before
    assert metadata_path.read_bytes() == metadata_before
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.rollback")) == []


def test_new_key_acl_failure_leaves_no_final_key_or_sensitive_temp(tmp_path, monkeypatch):
    def fail_acl(_path):
        raise OSError("synthetic ACL failure")

    monkeypatch.setattr(certificate_module, "_restrict_private_key", fail_acl)
    with pytest.raises(OSError, match="synthetic ACL failure"):
        ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    assert not (tmp_path / "mobile-sync-key.pem").exists()
    assert not (tmp_path / "mobile-sync-cert.pem").exists()
    assert not (tmp_path / "mobile-sync-certificate.json").exists()
    assert list(tmp_path.iterdir()) == []


def test_interrupted_certificate_tuple_is_rolled_back_on_restart(tmp_path):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    metadata_path = tmp_path / "mobile-sync-certificate.json"
    old_cert = cert_path.read_bytes()
    old_metadata = metadata_path.read_bytes()
    cert_path.with_suffix(".rollback").write_bytes(old_cert)
    metadata_path.with_suffix(".rollback").write_bytes(old_metadata)
    (tmp_path / certificate_module.TRANSACTION_NAME).write_text(
        json.dumps({"cert_existed": True, "metadata_existed": True}),
        encoding="utf-8",
    )
    cert_path.write_bytes(b"synthetic partial replacement")
    metadata_path.write_text('{"hosts":["192.168.50.11"]}', encoding="utf-8")

    recovered = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    assert recovered["created"] is False
    assert cert_path.read_bytes() == old_cert
    assert metadata_path.read_bytes() != b'{"hosts":["192.168.50.11"]}'
    assert not (tmp_path / certificate_module.TRANSACTION_NAME).exists()
    assert list(tmp_path.glob("*.rollback")) == []


def test_rollback_failure_preserves_evidence_and_next_start_recovers(tmp_path, monkeypatch):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    key_path = Path(first["key_path"])
    metadata_path = tmp_path / "mobile-sync-certificate.json"
    before = (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes())
    original_path_replace = Path.replace
    original_os_replace = certificate_module.os.replace

    def fail_metadata_forward(source, target):
        if Path(target).name == "mobile-sync-certificate.json":
            raise OSError("synthetic metadata forward failure")
        return original_path_replace(source, target)

    def fail_certificate_restore(source, target):
        if Path(source).name == "mobile-sync-cert.restore.tmp":
            raise OSError("synthetic certificate restore failure")
        return original_os_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_metadata_forward)
    monkeypatch.setattr(certificate_module.os, "replace", fail_certificate_restore)
    with pytest.raises(OSError, match="synthetic certificate restore failure"):
        ensure_certificate(
            tmp_path,
            ["192.168.50.11"],
            valid_days=30,
            renew_before_days=5,
            allow_address_reissue=True,
        )

    assert (tmp_path / certificate_module.TRANSACTION_NAME).is_file()
    assert (tmp_path / "mobile-sync-cert.rollback").is_file()
    assert (tmp_path / "mobile-sync-certificate.rollback").is_file()
    assert key_path.read_bytes() == before[1]

    monkeypatch.setattr(Path, "replace", original_path_replace)
    monkeypatch.setattr(certificate_module.os, "replace", original_os_replace)
    certificate_module._recover_interrupted_transaction(tmp_path)
    assert cert_path.read_bytes() == before[0]
    assert key_path.read_bytes() == before[1]
    assert metadata_path.read_bytes() == before[2]
    recovered = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    assert recovered["created"] is False
    assert not (tmp_path / certificate_module.TRANSACTION_NAME).exists()
    assert list(tmp_path.glob("*.rollback")) == []
    assert list(tmp_path.glob("*.restore.tmp")) == []


def test_missing_backup_and_corrupt_journal_block_without_mutating_tuple(tmp_path):
    first = ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    key_path = Path(first["key_path"])
    metadata_path = tmp_path / "mobile-sync-certificate.json"
    before = (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes())
    journal = tmp_path / certificate_module.TRANSACTION_NAME
    journal.write_text(json.dumps({
        "version": 2,
        "cert_existed": True,
        "metadata_existed": True,
        "new_key_created": False,
    }), encoding="utf-8")
    (tmp_path / "mobile-sync-cert.rollback").write_bytes(before[0])

    with pytest.raises(OSError, match="backup is missing"):
        ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    assert (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes()) == before
    assert journal.is_file()

    journal.write_text("{broken", encoding="utf-8")
    with pytest.raises(OSError, match="journal is invalid"):
        ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    assert (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes()) == before
    assert journal.read_text(encoding="utf-8") == "{broken"


def test_incomplete_journal_and_mismatched_certificate_backup_fail_closed(tmp_path):
    root = tmp_path / "active"
    other_root = tmp_path / "other"
    first = ensure_certificate(root, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    other = ensure_certificate(other_root, ["192.168.50.10"], valid_days=30, renew_before_days=5)
    cert_path = Path(first["cert_path"])
    key_path = Path(first["key_path"])
    metadata_path = root / "mobile-sync-certificate.json"
    before = (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes())
    journal = root / certificate_module.TRANSACTION_NAME

    journal.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError, match="invalid fields"):
        certificate_module._recover_interrupted_transaction(root)
    assert (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes()) == before
    assert journal.read_text(encoding="utf-8") == "{}"

    journal.write_text(json.dumps({
        "version": 2,
        "cert_existed": True,
        "metadata_existed": True,
        "new_key_created": False,
    }), encoding="utf-8")
    cert_path.with_suffix(".rollback").write_bytes(Path(other["cert_path"]).read_bytes())
    metadata_path.with_suffix(".rollback").write_bytes(before[2])
    with pytest.raises(OSError, match="does not match the private key"):
        certificate_module._recover_interrupted_transaction(root)
    assert (cert_path.read_bytes(), key_path.read_bytes(), metadata_path.read_bytes()) == before
    assert journal.is_file()
    assert cert_path.with_suffix(".rollback").is_file()
    assert metadata_path.with_suffix(".rollback").is_file()


@pytest.mark.parametrize("artifact_name", [
    certificate_module.TRANSACTION_NAME,
    "mobile-sync-cert.rollback",
    "mobile-sync-certificate.tmp",
    "mobile-sync-cert.restore.tmp",
])
def test_transaction_artifact_symlink_is_rejected_without_touching_external_target(tmp_path, artifact_name):
    root = tmp_path / artifact_name.replace(".", "-")
    root.mkdir()
    external = tmp_path / f"external-{artifact_name.replace('.', '-')}.txt"
    external.write_text("external-synthetic-target", encoding="utf-8")
    link = root / artifact_name
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows account")

    with pytest.raises(OSError, match="transaction artifact must not be a symlink or reparse point"):
        ensure_certificate(root, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    assert external.read_text(encoding="utf-8") == "external-synthetic-target"
    assert link.is_symlink()


def test_certificate_file_symlink_is_rejected_without_touching_target(tmp_path):
    target = tmp_path / "outside-key.pem"
    target.write_text("not-a-key", encoding="utf-8")
    key_path = tmp_path / "mobile-sync-key.pem"
    try:
        key_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows account")

    with pytest.raises(OSError, match="symlinks or reparse points"):
        ensure_certificate(tmp_path, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    assert target.read_text(encoding="utf-8") == "not-a-key"


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


def test_certificate_rollover_gate_reads_only_active_device_bindings(tmp_path):
    issued = ensure_certificate(tmp_path / "tls", ["192.168.50.10"], valid_days=30, renew_before_days=5)
    store = SyncStore(tmp_path / "personal.sqlite3")
    store.initialize()
    try:
        code = store.create_pairing_code("개인 PC", 60)
        paired = store.pair_device(code.code, "내 휴대폰", "192.0.2.10")
        assert tls_rollover_ready(store.path, Path(issued["cert_path"])) is False

        store.bind_tls_identity(store.authenticate(paired.token), issued["spki_sha256"])
        assert tls_rollover_ready(store.path, Path(issued["cert_path"])) is True
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


def test_android_continuity_ui_exposes_recovery_confirmation_and_binding_state():
    screens = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "insuranceai" / "android" / "ui" / "Screens.kt").read_text(encoding="utf-8")
    repository = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "insuranceai" / "android" / "sync" / "SyncRepository.kt").read_text(encoding="utf-8")

    assert "확인 전에는 코드가 전송되지 않습니다" in screens
    assert "자동 주소 전환 준비 실패" in screens
    assert "bindRemoteTlsIdentityBounded" in repository
    assert "runCatching { bindRemoteTlsIdentity" not in repository


def test_pc_ui_separates_revoked_device_recovery_from_first_pairing():
    panel = (ROOT / "desktop" / "src" / "components" / "MobileConnectionPanel.tsx").read_text(encoding="utf-8")

    assert "연결 해제된 모바일" in panel
    assert "이 기기 다시 연결" in panel
    assert "기존 기기와 미전송 자료를 유지" in panel
    assert "mobile_sync_create_recovery_code" in panel
    assert "token" not in panel.lower()
    assert "uuid" not in panel.lower()