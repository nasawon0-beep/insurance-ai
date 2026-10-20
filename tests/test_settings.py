"""항상 활성인 RRN 정책과 유지되어야 할 개인정보 보호 경계 테스트."""
import base64
import sqlite3

import pytest

pytest.importorskip("httpx")

_KEY = base64.b64encode(b"0" * 32).decode("ascii")
_RRN = "900101" + "1234567"  # 공개 테스트 전용 합성값
_INTAKE_RRN = "620805" + "1234567"  # 공개 테스트 전용 합성값


@pytest.fixture
def env(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "data" / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _KEY)
    monkeypatch.setenv("ENGINE_BACKUP", "0")
    monkeypatch.delenv("RRN_INPUT_ENABLED", raising=False)
    crypto.reset_cache()
    yield tmp_path
    crypto.reset_cache()


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def test_settings_reports_always_enabled_policy(client):
    assert client.get("/settings").json() == {
        "rrn_input_enabled": True,
        "source": "hardcoded",
    }


def test_patch_cannot_disable_always_enabled_policy(client):
    response = client.patch("/settings", json={"rrn_input_enabled": False})
    assert response.status_code == 200
    assert response.json() == {"rrn_input_enabled": True, "source": "hardcoded"}
    assert client.get("/settings").json()["rrn_input_enabled"] is True


@pytest.mark.parametrize("legacy_value", ["0", "1"])
def test_legacy_environment_toggle_cannot_override_policy(client, monkeypatch, legacy_value):
    monkeypatch.setenv("RRN_INPUT_ENABLED", legacy_value)
    assert client.get("/settings").json() == {
        "rrn_input_enabled": True,
        "source": "hardcoded",
    }


def test_invalid_rrn_is_still_rejected(client):
    response = client.post("/customers", json={"name": "형식검증", "rrn": "이건-형식도-엉망"})
    assert response.status_code == 422
    assert "숫자 13자리" in response.text


def test_rrn_is_saved_encrypted_and_list_response_remains_masked(client, env):
    created = client.post("/customers", json={"name": "합성암호화대상", "rrn": _RRN})
    assert created.status_code == 201 and created.json()["has_rrn"] is True
    cid = created.json()["id"]

    listed = client.get("/customers").json()["customers"][0]
    assert listed["id"] == cid
    assert listed["rrn"] is None and listed["rrn_masked"] is None
    assert listed["has_rrn"] is True

    db_path = env / "data" / "customers.sqlite3"
    assert _RRN.encode() not in db_path.read_bytes()
    with sqlite3.connect(db_path) as conn:
        encrypted, rrn_hash = conn.execute(
            "SELECT rrn, rrn_hash FROM customers WHERE id = ?", (cid,)
        ).fetchone()
    assert encrypted and encrypted != _RRN
    assert rrn_hash and rrn_hash != _RRN


def test_detail_and_reveal_keep_access_audit(client, env):
    created = client.post(
        "/customers",
        json={"name": "합성접근기록", "rrn": _RRN},
        headers={"X-Actor-Id": "creator"},
    ).json()
    cid = created["id"]
    detail = client.get(f"/customers/{cid}", headers={"X-Actor-Id": "viewer"})
    assert detail.status_code == 200 and detail.json()["rrn"] == _RRN

    assert client.get(f"/customers/{cid}/rrn").status_code == 422
    revealed = client.get(
        f"/customers/{cid}/rrn",
        params={"purpose": "보험 업무 확인"},
        headers={"X-Actor-Id": "revealer"},
    )
    assert revealed.status_code == 200 and revealed.json()["rrn"] == _RRN

    with sqlite3.connect(env / "data" / "customers.sqlite3") as conn:
        rows = conn.execute(
            "SELECT actor, access_type, purpose FROM rrn_access_log WHERE customer_id = ?",
            (cid,),
        ).fetchall()
    assert ("creator", "create_response", "고객 저장 응답에 주민등록번호 포함") in rows
    assert ("viewer", "detail_open", "고객 상세 화면 열람") in rows
    assert ("revealer", "explicit_reveal", "보험 업무 확인") in rows


def test_intake_keeps_rrn_under_always_enabled_policy(client, monkeypatch):
    from database import intake

    monkeypatch.setattr(
        intake,
        "_call_llm",
        lambda *a, **k: {"name": "합성인테이크", "rrn": _INTAKE_RRN, "birth_date": "1962-08-05"},
    )
    parsed = client.post("/customers/intake/parse", json={"text": f"합성인테이크 {_INTAKE_RRN}"}).json()
    assert parsed["fields"]["rrn"] == _INTAKE_RRN


def test_rrn_never_appears_in_general_audit_fields(client):
    customer = client.post("/customers", json={"name": "감사보호"}).json()
    assert client.patch(
        f"/customers/{customer['id']}", json={"rrn": _RRN}
    ).status_code == 200
    audit = client.get(f"/customers/{customer['id']}/audit").json()
    assert all("rrn" not in (row.get("fields") or "").lower() for row in audit)
    assert _RRN not in str(audit)
