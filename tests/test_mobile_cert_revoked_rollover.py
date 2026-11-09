"""PC IP가 바뀌었고 모든 모바일이 '연결 해제' 상태(옛 페어링이라 TLS identity 미바인딩)일 때:
- 이전: ensure-certificate가 'certificate confirmation required'로 막혀 모바일 서버(18443)가 뜨지 않고,
  '이 기기 다시 연결'(복구 코드) 버튼도 서버가 꺼져 있어 비활성 → 복구 불가 교착.
- 수정: 활성 기기가 0이면 같은 개인키로 새 주소 인증서를 재발급(SPKI 불변). 해제 기기는 복구 코드 + 새 주소·인증서 SHA-256
  직접 대조로만 복귀. 활성 미바인딩 기기가 있으면 여전히 거부(기존 보호 유지).
실서비스·운영 파일 없음(tmp_path 합성).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

from lan_sync.admin import tls_rollover_ready  # noqa: E402
from lan_sync.certificate import ensure_certificate  # noqa: E402
from lan_sync.store import SyncStore  # noqa: E402

OLD_IP, NEW_IP = "192.168.219.106", "192.168.0.73"


def _admin(db: Path, state: Path, host: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lan_sync.admin", "--db", str(db), "ensure-certificate", "--state-dir", str(state), "--host", host],
        cwd=ROOT / "local-engine", capture_output=True, text=True, timeout=60,
    )


def _paired_unbound(store: SyncStore, name: str) -> str:
    code = store.create_pairing_code("개인 PC", 60)
    return store.pair_device(code.code, name, "192.0.2.10").device_id


def test_all_revoked_unbound_devices_allow_same_key_reissue_and_recovery(tmp_path):
    state = tmp_path / "mobile-sync"
    first = ensure_certificate(state, [OLD_IP])
    key_before = Path(first["key_path"]).read_bytes()
    db = tmp_path / "customers.sqlite3"
    store = SyncStore(db)
    store.initialize()
    try:
        devices = [_paired_unbound(store, "Android"), _paired_unbound(store, "Android")]
        for device in devices:
            store.revoke_device(device)
    finally:
        store.close()
    assert tls_rollover_ready(db, Path(first["cert_path"])) is True

    result = _admin(db, state, NEW_IP)
    assert result.returncode == 0, result.stderr[-400:]
    issued = json.loads(result.stdout.strip().splitlines()[-1])
    assert issued["hosts"] == [NEW_IP] and issued["reason"] == "address_changed"
    assert issued["spki_sha256"] == first["spki_sha256"]  # 같은 키 → 모바일 identity 불변
    assert issued["fingerprint"] != first["fingerprint"]  # leaf는 새것 → 모바일이 PC 화면 값으로 대조
    assert Path(issued["key_path"]).read_bytes() == key_before

    store = SyncStore(db)
    store.initialize()
    try:
        code = store.create_recovery_code(devices[0], "0" * 32, 120)
        assert len(code.code) == 6
        assert all(d["revoked_at"] is not None for d in store.list_devices())  # 기기 기록 보존, 자동 복귀 없음
    finally:
        store.close()


def test_active_unbound_device_still_blocks_address_reissue(tmp_path):
    state = tmp_path / "mobile-sync"
    first = ensure_certificate(state, [OLD_IP])
    cert_before = Path(first["cert_path"]).read_bytes()
    db = tmp_path / "customers.sqlite3"
    store = SyncStore(db)
    store.initialize()
    try:
        _paired_unbound(store, "Android")
    finally:
        store.close()
    assert tls_rollover_ready(db, Path(first["cert_path"])) is False
    result = _admin(db, state, NEW_IP)
    assert result.returncode != 0
    assert "certificate confirmation required" in result.stderr
    assert Path(first["cert_path"]).read_bytes() == cert_before


def test_without_database_gate_still_requires_confirmation(tmp_path):
    first = ensure_certificate(tmp_path, [OLD_IP])
    with pytest.raises(ValueError, match="confirmation required"):
        ensure_certificate(tmp_path, [NEW_IP])
    assert tls_rollover_ready(tmp_path / "missing.sqlite3", Path(first["cert_path"])) is False
