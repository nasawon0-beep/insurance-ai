"""이 PC 기존 계정 ↔ 모아둠 계정 연결(양쪽 서버 확인) — Supabase 응답은 urlopen 대역으로 흉내(mock)."""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("httpx")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CENTRAL_USERS = {
    "tok-a": {"id": "11111111-1111-4111-8111-111111111111", "email": "Member@Example.test", "email_confirmed_at": "2026-11-02T00:00:00Z"},
    "tok-b": {"id": "22222222-2222-4222-8222-222222222222", "email": "other@example.test", "email_confirmed_at": "2026-11-02T00:00:00Z"},
    "tok-unconfirmed": {"id": "33333333-3333-4333-8333-333333333333", "email": "new@example.test", "email_confirmed_at": None},
}


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_DB_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("CONTROL_JWT_SECRET", "test-jwt")
    monkeypatch.setenv("CONTROL_LICENSE_SECRET", "test-lic")
    monkeypatch.setenv("CONTROL_ADMIN_TOKEN", "test-admin")
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        assert req.full_url == "https://example-ref.supabase.co/auth/v1/user"
        assert req.get_header("Apikey") == "sb_publishable_test"
        token = req.get_header("Authorization").split(" ", 1)[1]
        if token == "tok-down":
            raise urllib.error.URLError("offline")
        user = CENTRAL_USERS.get(token)
        if not user:
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b"{}"))
        return FakeResponse(json.dumps(user).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)
    c.seen = seen
    return c


def local_login(client, email="local@example.test", pw="secret1"):
    r = client.post("/auth/register", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def enable_central(monkeypatch):
    monkeypatch.setenv("CONTROL_CENTRAL_URL", "https://example-ref.supabase.co")
    monkeypatch.setenv("CONTROL_CENTRAL_PUBLISHABLE_KEY", "sb_publishable_test")


def h(t):
    return {"Authorization": f"Bearer {t}"}


def test_link_requires_both_logins_and_is_idempotent(client, monkeypatch):
    tok = local_login(client)  # 기존 설치본 계정(중앙 설정 전 가입)
    enable_central(monkeypatch)
    assert client.post("/central-link", json={"central_access_token": "tok-a"}).status_code == 401  # 로컬 미인증
    assert client.post("/central-link", json={"central_access_token": "bad"}, headers=h(tok)).status_code == 401
    r = client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(tok))
    assert r.status_code == 200, r.text
    assert r.json() == {"linked": True, "local_email": "local@example.test", "central_email": "member@example.test"}
    assert client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(tok)).status_code == 200
    assert client.get("/central-link", headers=h(tok)).json()["link"]["central_email"] == "member@example.test"
    # 다른 중앙 계정으로 덮어쓰기 금지
    assert client.post("/central-link", json={"central_access_token": "tok-b"}, headers=h(tok)).status_code == 409


def test_link_rejects_unconfirmed_email_and_reports_network_failure(client, monkeypatch):
    tok = local_login(client)
    enable_central(monkeypatch)
    assert client.post("/central-link", json={"central_access_token": "tok-unconfirmed"}, headers=h(tok)).status_code == 403
    assert client.post("/central-link", json={"central_access_token": "tok-down"}, headers=h(tok)).status_code == 503
    assert client.get("/central-link", headers=h(tok)).json()["link"] is None


def test_one_central_account_cannot_claim_two_local_accounts(client, monkeypatch):
    t1 = local_login(client, "one@example.test")
    t2 = local_login(client, "two@example.test")
    enable_central(monkeypatch)
    assert client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(t1)).status_code == 200
    assert client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(t2)).status_code == 409


def test_central_build_closes_local_signup_but_keeps_local_login(client, monkeypatch):
    local_login(client, "legacy@example.test")
    enable_central(monkeypatch)
    r = client.post("/auth/register", json={"email": "new@example.test", "password": "secret1"})
    assert r.status_code == 403
    assert client.post("/auth/login", json={"email": "legacy@example.test", "password": "secret1"}).status_code == 200


def test_link_unavailable_without_central_config(client):
    tok = local_login(client)
    assert client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(tok)).status_code == 503
    assert client.seen == []  # 설정이 없으면 외부로 요청하지 않음


def test_plain_http_central_url_only_in_official_qa_mode(client, monkeypatch):
    tok = local_login(client)
    monkeypatch.setenv("CONTROL_CENTRAL_URL", "http://127.0.0.1:54399")
    monkeypatch.setenv("CONTROL_CENTRAL_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.delenv("INSURANCE_AI_QA_MODE", raising=False)
    assert client.post("/central-link", json={"central_access_token": "tok-a"}, headers=h(tok)).status_code == 503
    import main

    monkeypatch.setenv("INSURANCE_AI_QA_MODE", "1")
    assert main._central_url() == "http://127.0.0.1:54399"  # 공식 QA 프로필 + loopback만
    monkeypatch.setenv("CONTROL_CENTRAL_URL", "http://evil.example:80")
    assert main._central_url() == ""
    monkeypatch.delenv("INSURANCE_AI_QA_MODE")
    monkeypatch.setenv("CONTROL_CENTRAL_URL", "https://abc.supabase.co")
    assert main._central_url() == "https://abc.supabase.co"
    assert client.seen == []


def test_central_build_local_account_cannot_obtain_local_license(client, monkeypatch):
    """중앙 빌드에서 이 PC 기존 계정 로그인만으로 로컬(고정 개발 서명) 이용권을 받아 중앙 이용권을 우회하면 안 된다.
    로컬 비밀번호 확인(/auth/login)은 '중앙 로그인 후 계정 연결'에만 쓰이므로 유지한다. 계정/자료는 지우지 않는다."""
    tok = local_login(client, "legacy2@example.test", "secret1")  # 중앙 설정 전 기존 계정
    monkeypatch.setenv("CONTROL_CENTRAL_URL", "https://example-ref.supabase.co")
    monkeypatch.setenv("CONTROL_CENTRAL_PUBLISHABLE_KEY", "sb_publishable_test")
    login = client.post("/auth/login", json={"email": "legacy2@example.test", "password": "secret1"})
    assert login.status_code == 200
    lt = login.json()["token"]
    r = client.get("/license", headers={"Authorization": f"Bearer {lt}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "central_account_required"
    assert "signed" not in r.text
    # 연결은 여전히 가능(중앙 인증 후)
    r = client.post("/central-link", headers={"Authorization": f"Bearer {lt}"}, json={"central_access_token": "tok-a"})
    assert r.status_code == 200 and r.json()["linked"] is True
    # 중앙 설정이 없는 로컬 전용 빌드는 기존 동작 그대로
    monkeypatch.delenv("CONTROL_CENTRAL_URL")
    r = client.get("/license", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["status"] == "active" and "signed" in r.json()
