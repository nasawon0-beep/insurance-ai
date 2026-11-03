"""중앙 빌드: 로컬 재설정 차단 + 자료 보존 모드 로그인 근거 테스트 (test_central_link.py 의 fixture 재사용)."""
from fastapi.testclient import TestClient

from test_central_link import client, enable_central, local_login  # noqa: F401  (pytest fixture 재사용)


def _loopback(app):
    async def loopback_app(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8790)}
        await app(scope, receive, send)

    return TestClient(loopback_app)


def test_central_build_disables_unauthenticated_local_password_reset(client, monkeypatch):  # noqa: F811
    """중앙 빌드에서 이 PC 계정은 '자료 보존 모드'(조회·내보내기) 로그인 근거다 →
    같은 PC의 인증 없는 로컬 재설정으로 남의 계정을 열 수 없어야 한다. 로컬 전용 빌드 복구는 그대로."""
    import main

    local_login(client, "victim@example.test", "secret1")
    monkeypatch.setenv("CONTROL_LOCAL_RECOVERY", "1")
    with _loopback(main.app) as lc:
        assert lc.get("/auth/local-recovery/status").status_code == 200  # 로컬 전용 빌드: 기존 동작
        enable_central(monkeypatch)
        for method, path, body in (
            ("get", "/auth/local-recovery/status", None),
            ("get", "/auth/local-recovery/accounts", None),
            ("post", "/auth/local-recovery/reset", {"email": "victim@example.test", "new_password": "attacker1"}),
        ):
            r = getattr(lc, method)(path, json=body) if body else getattr(lc, method)(path)
            assert r.status_code == 404, (path, r.status_code)
    assert client.post("/auth/login", json={"email": "victim@example.test", "password": "secret1"}).status_code == 200
    assert client.post("/auth/login", json={"email": "victim@example.test", "password": "attacker1"}).status_code == 401


def test_central_build_legacy_session_check_uses_auth_me_only(client, monkeypatch):  # noqa: F811
    """자료 보존 모드 판정은 로컬 비밀번호로 받은 세션의 /auth/me 확인만 쓰고, 이용권(/license)은 계속 거부."""
    tok = local_login(client, "legacy3@example.test", "secret1")
    enable_central(monkeypatch)
    h = {"Authorization": f"Bearer {tok}"}
    me = client.get("/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["user"]["email"] == "legacy3@example.test"
    assert client.get("/license", headers=h).status_code == 403
    assert client.get("/auth/me", headers={"Authorization": "Bearer forged.token.value"}).status_code == 401
    assert client.get("/auth/me").status_code in (401, 403)
