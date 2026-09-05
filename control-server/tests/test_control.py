"""control-server 계약 테스트: 인증 / 라이선스(+서명) / 기기 / 업데이트 / 오류수집."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("CONTROL_JWT_SECRET", "test-jwt")
    monkeypatch.setenv("CONTROL_LICENSE_SECRET", "test-lic")
    monkeypatch.setenv("CONTROL_ADMIN_TOKEN", "test-admin")
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def _auth(client, email="a@b.com", pw="secret1"):
    r = client.post("/auth/register", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]["id"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


def admin_h():
    return {"X-Control-Admin-Token": "test-admin"}


def _loopback_client(app, server=("127.0.0.1", 8790)):
    """현재 Starlette TestClient에는 client= 인자가 없어 HTTP scope만 등가 조정한다."""
    from fastapi.testclient import TestClient

    async def loopback_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": ("127.0.0.1", 1), "server": server}
        await app(scope, receive, send)

    return TestClient(loopback_app)


def test_register_login_me(client):
    tok, uid = _auth(client)
    assert client.post("/auth/register", json={"email": "a@b.com", "password": "secret1"}).status_code == 409

    bad = client.post("/auth/login", json={"email": "a@b.com", "password": "wrongpw"})
    assert bad.status_code == 401
    good = client.post("/auth/login", json={"email": "a@b.com", "password": "secret1"})
    assert good.status_code == 200

    assert client.get("/auth/me").status_code == 401
    me = client.get("/auth/me", headers=h(tok)).json()["user"]
    assert me["id"] == uid
    assert me["email"] == "a@b.com"
    assert me["created_at"].startswith("20")


def test_me_returns_401_when_user_is_deleted(client):
    tok, uid = _auth(client)
    from db import connect

    conn = connect()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()

    assert client.get("/auth/me", headers=h(tok)).status_code == 401


def test_license_default_and_signed_blob(client):
    tok, uid = _auth(client)
    lic = client.get("/license", headers=h(tok)).json()
    assert lic["plan"] == "ACTIVE"
    assert lic["status"] == "active" and lic["days_left"] > 0
    assert lic["expiry"]

    # 서명 블롭 검증
    from security import verify_license_blob

    assert verify_license_blob(lic["signed"]) is True
    tampered = {**lic["signed"], "license": {**lic["signed"]["license"], "plan": "PRO"}}
    assert verify_license_blob(tampered) is False


def test_admin_license_expiry_makes_expired(client):
    tok, uid = _auth(client)
    unauthorized = client.patch(f"/admin/license/{uid}", json={"plan": "PRO"})
    assert unauthorized.status_code == 401

    r = client.patch(
        f"/admin/license/{uid}",
        json={"plan": "PRO", "expiry": "2000-01-01"},
        headers=admin_h(),
    )
    assert r.status_code == 200 and r.json()["plan"] == "PRO"
    lic = client.get("/license", headers=h(tok)).json()
    assert lic["status"] == "expired" and lic["days_left"] < 0

    # 무기한
    client.patch(f"/admin/license/{uid}", json={"expiry": ""}, headers=admin_h())
    lic = client.get("/license", headers=h(tok)).json()
    assert lic["expiry"] is None and lic["status"] == "active"


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("CONTROL_JWT_SECRET", "CONTROL_JWT_SECRET"),
        ("CONTROL_LICENSE_SECRET", "CONTROL_LICENSE_SECRET"),
    ],
)
def test_production_requires_secrets(monkeypatch, missing, expected):
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_JWT_SECRET", "test-jwt")
    monkeypatch.setenv("CONTROL_LICENSE_SECRET", "test-license")
    monkeypatch.delenv(missing)
    result = subprocess.run(
        [sys.executable, "-c", "import security"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert f"RuntimeError: {expected} must be set" in result.stderr


def test_development_secret_fallback_warns(monkeypatch):
    monkeypatch.setenv("CONTROL_ENV", "development")
    monkeypatch.delenv("CONTROL_JWT_SECRET", raising=False)
    monkeypatch.delenv("CONTROL_LICENSE_SECRET", raising=False)
    result = subprocess.run(
        [sys.executable, "-c", "import security"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "WARNING: CONTROL_JWT_SECRET is unset" in result.stderr
    assert "WARNING: CONTROL_LICENSE_SECRET is unset" in result.stderr


def test_cors_restricts_origins(client):
    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"

    denied = client.options(
        "/health",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in denied.headers


def test_device_limit_and_reregister(client):
    tok, uid = _auth(client)
    for i in range(2):
        r = client.post("/devices", json={"device_id": f"dev-{i}", "name": f"PC{i}"}, headers=h(tok))
        assert r.status_code == 201
    # 3번째 초과
    assert client.post("/devices", json={"device_id": "dev-2"}, headers=h(tok)).status_code == 409
    # 기존 기기 재등록은 갱신 (수 안 늘어남)
    again = client.post("/devices", json={"device_id": "dev-0", "name": "PC0-renamed", "app_version": "0.2.0"}, headers=h(tok))
    assert again.status_code == 201 and again.json()["name"] == "PC0-renamed"
    assert len(client.get("/devices", headers=h(tok)).json()["devices"]) == 2

    # 해제 후 새 기기 등록 가능
    dev0_id = client.get("/devices", headers=h(tok)).json()["devices"][0]["id"]
    assert client.delete(f"/devices/{dev0_id}", headers=h(tok)).status_code == 204
    assert client.post("/devices", json={"device_id": "dev-new"}, headers=h(tok)).status_code == 201


def test_devices_are_per_user(client):
    tok_a, _ = _auth(client, "a@x.com")
    tok_b, _ = _auth(client, "b@x.com")
    client.post("/devices", json={"device_id": "d1"}, headers=h(tok_a))
    assert client.get("/devices", headers=h(tok_b)).json()["devices"] == []


def test_update_check(client):
    r = client.get("/update/check", params={"version": "0.1.0", "channel": "STABLE"})
    assert r.status_code == 200
    assert r.json()["latest"] == "0.1.0" and r.json()["up_to_date"] is True
    assert client.get("/update/check", params={"version": "0.0.1"}).json()["up_to_date"] is False


def test_change_password(client):
    tok, _ = _auth(client, "chg@x.com", "oldpass1")
    # 새 비밀번호가 6자 미만이면 422
    assert client.post("/auth/change-password", json={"current_password": "oldpass1", "new_password": "12345"}, headers=h(tok)).status_code == 422
    # 현재 비번 틀리면 401
    assert client.post("/auth/change-password", json={"current_password": "nope123", "new_password": "newpass1"}, headers=h(tok)).status_code == 401
    # 정상 변경
    assert client.post("/auth/change-password", json={"current_password": "oldpass1", "new_password": "newpass1"}, headers=h(tok)).status_code == 200
    assert client.post("/auth/login", json={"email": "chg@x.com", "password": "oldpass1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "chg@x.com", "password": "newpass1"}).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/auth/local-recovery/status", None),
        ("get", "/auth/local-recovery/accounts", None),
        ("post", "/auth/local-recovery/reset", {"email": "a@b.com", "new_password": "newpass1"}),
    ],
)
def test_local_recovery_flag_off_is_hidden(client, monkeypatch, method, path, body):
    monkeypatch.delenv("CONTROL_LOCAL_RECOVERY", raising=False)
    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/auth/local-recovery/status", None),
        ("get", "/auth/local-recovery/accounts", None),
        ("post", "/auth/local-recovery/reset", {"email": "a@b.com", "new_password": "newpass1"}),
    ],
)
def test_local_recovery_rejects_non_loopback(client, monkeypatch, method, path, body):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert response.status_code == 404


def test_local_recovery_lists_and_resets_account(client, monkeypatch):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    _auth(client, "Recover@Example.com", "oldpass1")
    import main

    with _loopback_client(main.app) as local_client:
        assert local_client.get("/auth/local-recovery/status").json() == {"available": True}
        accounts = local_client.get("/auth/local-recovery/accounts")
        assert accounts.status_code == 200
        account = next(a for a in accounts.json()["accounts"] if a["email"] == "recover@example.com")
        assert account["created_at"].startswith("20")
        assert "id" not in account and "pw_hash" not in account

        reset = local_client.post(
            "/auth/local-recovery/reset",
            json={"email": " Recover@Example.com ", "new_password": "newpass1"},
        )
        assert reset.status_code == 200
        assert reset.json() == {"reset": True, "email": "recover@example.com"}
        assert local_client.post(
            "/auth/login", json={"email": "recover@example.com", "password": "oldpass1"}
        ).status_code == 401
        assert local_client.post(
            "/auth/login", json={"email": "recover@example.com", "password": "newpass1"}
        ).status_code == 200


def test_local_recovery_rejects_forwarded_header(client, monkeypatch):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    import main

    with _loopback_client(main.app) as local_client:
        response = local_client.get(
            "/auth/local-recovery/status", headers={"X-Forwarded-For": "127.0.0.1"}
        )
        assert response.status_code == 404


def test_local_recovery_rejects_non_loopback_bind(client, monkeypatch):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    import main

    with _loopback_client(main.app, ("192.168.1.10", 8790)) as local_client:
        response = local_client.get("/auth/local-recovery/status")
        assert response.status_code == 404


@pytest.mark.parametrize("header", ["Forwarded", "X-Real-IP"])
def test_local_recovery_rejects_other_forwarding_headers(client, monkeypatch, header):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    import main

    with _loopback_client(main.app) as local_client:
        response = local_client.get(
            "/auth/local-recovery/status", headers={header: "for=127.0.0.1"}
        )
        assert response.status_code == 404


def test_local_recovery_invalid_reset_does_not_change_db(client, monkeypatch):
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    _auth(client, "unchanged@example.com", "oldpass1")
    from db import connect
    import main

    conn = connect()
    try:
        original_hash = conn.execute(
            "SELECT pw_hash FROM users WHERE email = ?", ("unchanged@example.com",)
        ).fetchone()["pw_hash"]
    finally:
        conn.close()

    with _loopback_client(main.app) as local_client:
        too_short = local_client.post(
            "/auth/local-recovery/reset",
            json={"email": "unchanged@example.com", "new_password": "12345"},
        )
        assert too_short.status_code == 422
        missing = local_client.post(
            "/auth/local-recovery/reset",
            json={"email": "missing@example.com", "new_password": "newpass1"},
        )
        assert missing.status_code == 404

    conn = connect()
    try:
        current_hash = conn.execute(
            "SELECT pw_hash FROM users WHERE email = ?", ("unchanged@example.com",)
        ).fetchone()["pw_hash"]
    finally:
        conn.close()
    assert current_hash == original_hash
    assert client.post(
        "/auth/login", json={"email": "unchanged@example.com", "password": "oldpass1"}
    ).status_code == 200


def test_reset_password_cli(client, tmp_path, monkeypatch):
    """오프라인 재설정 스크립트가 DB를 직접 고친다."""
    _auth(client, "forgot@x.com", "oldpass1")  # client fixture 가 CONTROL_DB_PATH 설정함
    import importlib

    rp = importlib.import_module("reset_password")
    monkeypatch.setattr("sys.argv", ["reset_password.py", "forgot@x.com", "brandnew1"])
    assert rp.main() == 0
    assert client.post("/auth/login", json={"email": "forgot@x.com", "password": "brandnew1"}).status_code == 200


def test_error_report_pii_safe(client):
    tok, _ = _auth(client)
    assert client.post("/error-report", json={"code": "E42", "message": "parser failed", "app_version": "0.1.0"}).status_code == 201
    assert client.post("/error-report", json={"code": "E7"}, headers=h(tok)).status_code == 201
