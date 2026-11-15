"""엔진 인증 테스트"""
import base64
import os
import pytest
from synthetic_account_binding import AUTHORIZATION, FIELDS, bind_synthetic_account

# 이 테스트는 인증을 확인하므로 DEV_SKIP_AUTH를 비활성화
os.environ["DEV_SKIP_AUTH"] = "0"


def test_api_requires_secret_header():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.get("/settings")
    assert response.status_code == 401


def test_qa_request_binding_rejects_port_swap_before_customer_mutation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from database import crypto
    import main

    db_path = tmp_path / "customers.sqlite3"
    instance_id = "12345678-1234-1234-1234-123456789abc"
    fingerprint = "a" * 64
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", base64.b64encode(b"q" * 32).decode())
    monkeypatch.setenv("INSURANCE_AI_QA_MODE", "1")
    monkeypatch.setenv("INSURANCE_AI_INSTANCE_ID", instance_id)
    monkeypatch.setenv("INSURANCE_AI_PROFILE_FINGERPRINT", fingerprint)
    crypto.reset_cache()
    bind_synthetic_account(monkeypatch, main.app)
    client = TestClient(main.app)
    headers = {
        "Authorization": AUTHORIZATION,
        main.API_SECRET_HEADER: main.API_SECRET,
        "X-Insurance-AI-QA-Instance": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "X-Insurance-AI-QA-Binding": fingerprint,
    }
    rejected = client.post("/customers", json={"name": "synthetic"}, headers=headers)
    assert rejected.status_code == 403
    assert not db_path.exists()

    headers["X-Insurance-AI-QA-Instance"] = instance_id
    for authorization in ('', 'Bearer synthetic-foreign-account'):
        denied = client.post('/customers', json={'name': 'synthetic'},
                             headers={**headers, 'Authorization': authorization})
        assert denied.status_code == 401
        assert not db_path.exists()
    accepted = client.post("/customers", json={"name": "synthetic"}, headers=headers)
    assert accepted.status_code == 201
    assert db_path.exists()
    crypto.reset_cache()


@pytest.mark.parametrize('fault', ['unbound', 'missing-bearer', 'foreign-bearer', 'expired'])
def test_account_rejection_precedes_customer_database_creation(tmp_path, monkeypatch, fault):
    from fastapi.testclient import TestClient
    import main
    db_path = tmp_path / 'denied.sqlite3'
    monkeypatch.setenv('CUSTOMER_DB_PATH', str(db_path))
    bind_synthetic_account(monkeypatch, main.app, expires_at=0 if fault == 'expired' else 4102444800)
    if fault == 'unbound':
        for name in FIELDS:
            monkeypatch.delenv(name, raising=False)
    client = TestClient(main.app)
    client.headers.pop('Authorization', None)
    auth = '' if fault == 'missing-bearer' else ('Bearer synthetic-foreign-account' if fault == 'foreign-bearer' else AUTHORIZATION)
    response = client.post('/customers', json={'name': 'DENIED SYNTHETIC'},
                           headers={'Authorization': auth, 'X-Actor-Id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'})
    assert response.status_code == 401
    assert not db_path.exists()


def test_health_remains_available_without_secret(monkeypatch):
    from fastapi.testclient import TestClient
    from database import crypto
    import main

    monkeypatch.setattr(
        crypto,
        "get_cipher",
        lambda: type("Cipher", (), {"key_source": "test"})(),
    )
    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.get("/health")
    assert response.status_code == 200


def test_api_secret_requires_allowed_origin():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    assert client.get("/api-secret").status_code == 200
    assert client.get("/api-secret", headers={"Origin": "https://example.com"}).status_code == 403
    response = client.get("/api-secret", headers={"Origin": "tauri://localhost"})
    assert response.status_code == 200
    assert response.json() == {"secret": main.API_SECRET}


def test_allowed_origin_preflight_succeeds():
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    del client.headers[main.API_SECRET_HEADER]
    response = client.options(
        "/settings",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": main.API_SECRET_HEADER,
        },
    )
    assert 200 <= response.status_code < 300


def test_dev_skip_auth_is_blocked_in_production(monkeypatch):
    import main

    monkeypatch.setenv("DEV_SKIP_AUTH", "1")
    monkeypatch.setenv("HERMES_ENV", "production")

    try:
        main._guard_dev_skip_auth()
    except RuntimeError as exc:
        assert "DEV_SKIP_AUTH" in str(exc)
    else:
        raise AssertionError("production DEV_SKIP_AUTH must block startup")


def test_dev_skip_auth_is_blocked_in_release(monkeypatch):
    import main

    monkeypatch.setenv("DEV_SKIP_AUTH", "1")
    monkeypatch.setenv("RELEASE", "true")

    try:
        main._guard_dev_skip_auth()
    except RuntimeError as exc:
        assert "RELEASE" in str(exc)
    else:
        raise AssertionError("release DEV_SKIP_AUTH must block startup")
