"""
Auth API 엔드포인트 통합 테스트
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile
import shutil
import os

@pytest.fixture
def client(monkeypatch):
    """FastAPI 테스트 클라이언트"""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "auth.db"

    monkeypatch.setenv("DEV_SKIP_AUTH", "1")  # API 테스트에서는 세션 검증 우회

    import auth
    original_path = auth.auth_db_path
    auth.auth_db_path = db_path
    auth.pwd_mgr = None
    auth.session_mgr = None

    from main import app
    try:
        yield TestClient(app)
    finally:
        auth.auth_db_path = original_path
        auth.pwd_mgr = None
        auth.session_mgr = None
        shutil.rmtree(tmpdir)


def _setup_password(client, password="TestPassword123"):
    response = client.post("/auth/setup", json={"password": password})
    assert response.status_code == 200
    return response


def test_auth_status_not_initialized(client):
    """초기화 상태 확인 - 미초기화"""
    response = client.get("/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert "initialized" in data


def test_auth_setup(client):
    """마스터 비밀번호 설정"""
    response = client.post("/auth/setup", json={"password": "TestPassword123"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


def test_auth_setup_duplicate(client):
    """중복 설정 방지"""
    _setup_password(client)
    response = client.post("/auth/setup", json={"password": "AnotherPassword"})
    assert response.status_code == 400
    assert "이미 설정" in response.json()["detail"]


def test_auth_setup_short_password(client):
    """짧은 비밀번호 거부"""
    response = client.post("/auth/setup", json={"password": "Short1"})
    assert response.status_code == 400
    assert "8자 이상" in response.json()["detail"]


def test_auth_login_success(client):
    """정상 로그인"""
    _setup_password(client)
    response = client.post("/auth/login", json={
        "password": "TestPassword123",
        "device_id": "test-device"
    })
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert len(data["session_id"]) > 20


def test_auth_login_wrong_password(client):
    """잘못된 비밀번호"""
    _setup_password(client)
    response = client.post("/auth/login", json={
        "password": "WrongPassword",
        "device_id": "test-device"
    })
    assert response.status_code == 401
    assert "틀렸습니다" in response.json()["detail"]


def test_auth_logout(client):
    """로그아웃"""
    _setup_password(client)
    # 로그인
    response = client.post("/auth/login", json={
        "password": "TestPassword123",
        "device_id": "test-device"
    })
    session_id = response.json()["session_id"]

    # 로그아웃
    response = client.post("/auth/logout", headers={"X-Session-ID": session_id})
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_expired_sessions_are_cleaned_on_access(client):
    _setup_password(client)
    import auth

    conn = auth._connect()
    try:
        conn.execute(
            "INSERT INTO sessions (id, device_id, created_at, last_accessed_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("expired-session", "old-device", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post("/auth/login", json={"password": "TestPassword123", "device_id": "new-device"})
    assert response.status_code == 200

    conn = auth._connect()
    try:
        assert conn.execute("SELECT 1 FROM sessions WHERE id = ?", ("expired-session",)).fetchone() is None
    finally:
        conn.close()


def test_auth_status_initialized(client):
    """초기화 상태 확인 - 초기화됨"""
    _setup_password(client)
    response = client.get("/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert data["initialized"] is True


def test_full_auth_flow_integration(client):
    """전체 인증 흐름 통합 테스트"""
    # 1. 상태 확인
    response = client.get("/auth/status")
    assert response.status_code == 200
    _setup_password(client)

    # 2. 로그인
    response = client.post("/auth/login", json={
        "password": "TestPassword123",
        "device_id": "integration-test"
    })
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    # 3. 세션으로 API 호출 (헬스체크)
    response = client.get("/health", headers={"X-Session-ID": session_id})
    assert response.status_code == 200

    # 4. 로그아웃
    response = client.post("/auth/logout", headers={"X-Session-ID": session_id})
    assert response.status_code == 200
