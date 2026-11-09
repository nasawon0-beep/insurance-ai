"""Wi-Fi↔핫스팟 왕복(주소 A→B→A)과 앱 재시작에서 실제 PC 경로(admin CLI ensure-certificate 게이트 + 실제 TLS 서버)로
등록 코드·주소·지문 재입력 없이 같은 토큰과 SPKI 핀만으로 이어지는지, 해제 기기는 자동 복귀하지 않는지 확인.
루프백 주소(127.0.0.1/127.0.0.2)와 tmp 합성 DB만 사용 — 운영 파일·실폰 없음.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))
sys.path.insert(0, str(ROOT / "tests"))

from lan_sync.app import create_app  # noqa: E402
from lan_sync.certificate import ensure_certificate  # noqa: E402
from lan_sync.store import SyncStore  # noqa: E402
from test_ip_continuity_tls_e2e import SyntheticTlsServer, verified_post  # noqa: E402

INSTANCE = "0123456789abcdef0123456789abcdef"
WIFI, HOTSPOT, PORT = "127.0.0.1", "127.0.0.2", 19447


def pc_ensure_certificate(db: Path, state: Path, host: str) -> dict:
    """앱(mobile_sync.rs)이 연결 시작 때 부르는 것과 같은 admin CLI."""
    result = subprocess.run(
        [sys.executable, "-m", "lan_sync.admin", "--db", str(db), "ensure-certificate", "--state-dir", str(state), "--host", host],
        cwd=ROOT / "local-engine", capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-400:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def serve(db: Path, cert: dict, host: str):
    app = create_app(db, enable_production_bridge=False, pc_instance_id=INSTANCE, tls_identity_spki_sha256=cert["spki_sha256"])
    return app, SyntheticTlsServer(app, host, PORT, cert["cert_path"], cert["key_path"])


def test_wifi_hotspot_round_trip_and_restart_reuse_token_and_spki_only(tmp_path):
    db, state = tmp_path / "customers.sqlite3", tmp_path / "mobile-sync"
    first = ensure_certificate(state, [WIFI])
    app, server = serve(db, first, WIFI)
    code = app.state.sync_store.create_pairing_code("합성 PC", 60)
    revoked_code = app.state.sync_store.create_pairing_code("합성 PC", 60)
    with server:
        # 첫 연결(유일한 수동 단계): 페어링 + TLS identity 바인딩
        status, phone = verified_post(WIFI, PORT, "/v1/pair", {"code": code.code, "device_name": "합성 폰"}, first["spki_sha256"])
        assert status == 200
        status, _ = verified_post(WIFI, PORT, "/v1/tls-identity/bind", {"spki_sha256": first["spki_sha256"]}, first["spki_sha256"], phone["token"])
        assert status == 200
        status, old_phone = verified_post(WIFI, PORT, "/v1/pair", {"code": revoked_code.code, "device_name": "예전 폰"}, first["spki_sha256"])
        assert status == 200
        status, _ = verified_post(WIFI, PORT, "/v1/tls-identity/bind", {"spki_sha256": first["spki_sha256"]}, first["spki_sha256"], old_phone["token"])
        assert status == 200
    store = SyncStore(db)
    store.initialize()
    try:
        store.revoke_device(old_phone["device_id"])  # 사용자가 해제한 기기
    finally:
        store.close()

    seen_leaf = {first["fingerprint"]}
    for hop, host in enumerate([HOTSPOT, WIFI, WIFI]):  # 핫스팟 → 다시 Wi-Fi → 같은 주소로 앱 재시작
        cert = pc_ensure_certificate(db, state, host)
        assert cert["hosts"] == [host]
        assert cert["spki_sha256"] == first["spki_sha256"]  # 신뢰 identity 불변
        if hop < 2:
            assert cert["reason"] == "address_changed" and cert["fingerprint"] not in seen_leaf
        else:
            assert cert["reason"] == "reused"  # 재시작은 재발급 없음
        seen_leaf.add(cert["fingerprint"])
        _, server = serve(db, cert, host)
        with server:
            # 폰은 저장된 토큰 + SPKI 핀만 사용(코드·주소 지문 입력 없음). verified_post가 SAN에 새 주소가 있는지도 확인.
            status, proof = verified_post(host, PORT, "/v1/continuity", {}, first["spki_sha256"], phone["token"])
            assert status == 200
            assert proof["device_id"] == phone["device_id"] and proof["pc_instance_id"] == INSTANCE
            # 해제 기기는 주소가 바뀌어도 자동 복귀하지 않음
            status, _ = verified_post(host, PORT, "/v1/continuity", {}, first["spki_sha256"], old_phone["token"])
            assert status == 401
    store = SyncStore(db)
    store.initialize()
    try:
        devices = {d["id"]: d for d in store.list_devices()}
    finally:
        store.close()
    assert devices[old_phone["device_id"]]["revoked_at"] is not None
    assert devices[phone["device_id"]]["revoked_at"] is None


def test_active_device_without_tls_identity_keeps_address_change_fail_closed(tmp_path):
    db, state = tmp_path / "customers.sqlite3", tmp_path / "mobile-sync"
    first = ensure_certificate(state, [WIFI])
    app, server = serve(db, first, WIFI)
    code = app.state.sync_store.create_pairing_code("합성 PC", 60)
    with server:
        status, _ = verified_post(WIFI, PORT, "/v1/pair", {"code": code.code, "device_name": "옛 앱 폰"}, first["spki_sha256"])
        assert status == 200  # 바인딩 안 함(구버전 앱)
    result = subprocess.run(
        [sys.executable, "-m", "lan_sync.admin", "--db", str(db), "ensure-certificate", "--state-dir", str(state), "--host", HOTSPOT],
        cwd=ROOT / "local-engine", capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0 and "certificate confirmation required" in result.stderr
    assert json.loads((state / "mobile-sync-certificate.json").read_text(encoding="utf-8"))["hosts"] == [WIFI]
