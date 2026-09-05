"""RRN 파일럿 토글 + /settings 테스트."""
import base64

import pytest

pytest.importorskip("httpx")

_KEY = base64.b64encode(b"0" * 32).decode("ascii")


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


def test_default_off(client):
    b = client.get("/settings").json()
    assert b["rrn_input_enabled"] is False
    assert b["source"] == "default"


def test_patch_reflects_local(client):
    r = client.patch("/settings", json={"rrn_input_enabled": True})
    assert r.status_code == 200
    assert r.json() == {"rrn_input_enabled": True, "source": "local"}
    assert client.get("/settings").json()["rrn_input_enabled"] is True
    client.patch("/settings", json={"rrn_input_enabled": False})
    assert client.get("/settings").json()["rrn_input_enabled"] is False


def test_env_locks_patch(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    assert client.get("/settings").json()["source"] == "env"
    r = client.patch("/settings", json={"rrn_input_enabled": True})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "개인정보보호법" in detail["message"]
    assert detail["locked_by"] == "env"


def test_env_on_wins(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    b = client.get("/settings").json()
    assert b["rrn_input_enabled"] is True and b["source"] == "env"


# ---------- 토글 OFF 하드 가드 ----------

def test_post_customers_ignores_body_rrn_no_422(client):
    r = client.post("/customers", json={"name": "무주민", "rrn": "이건-형식도-엉망"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_rrn"] is False
    assert body["rrn"] is None


def test_get_customers_carry_no_rrn(client, monkeypatch):
    # 먼저 ON 으로 주민번호를 심어두고
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    cid = client.post("/customers", json={"name": "가려짐", "rrn": "900101-1234567"}).json()["id"]
    assert client.get(f"/customers/{cid}").json()["rrn"] == "9001011234567"

    # OFF 로 되돌리면 응답에서 사라진다 (저장값은 그대로 — has_rrn 유지)
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    detail = client.get(f"/customers/{cid}").json()
    assert detail["rrn"] is None and detail["rrn_masked"] is None
    assert detail["has_rrn"] is True
    listed = client.get("/customers").json()["customers"][0]
    assert listed["rrn"] is None and listed["has_rrn"] is True


def test_patch_customers_ignores_body_rrn(client):
    cid = client.post("/customers", json={"name": "패치대상"}).json()["id"]
    r = client.patch(f"/customers/{cid}", json={"memo": "ok", "rrn": "900101-1234567"})
    assert r.status_code == 200
    assert r.json()["memo"] == "ok" and r.json()["has_rrn"] is False


def test_capture_and_intake_return_null_rrn(client, monkeypatch):
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"name": "정지은", "rrn": "620805-1234567", "birth_date": "1962-08-05"},
    )
    parsed = client.post("/customers/intake/parse", json={"text": "정지은 620805-1234567"}).json()
    assert parsed["fields"]["rrn"] is None
    assert not any("주민등록번호" in w for w in parsed["warnings"])

    monkeypatch.setattr(
        intake, "_call_llm", lambda *a, **k: {"customers": [{"name": "정지은", "rrn": "620805-1234567"}]}
    )
    cap = client.post("/capture", data={"text": "정지은 620805-1234567"}).json()
    assert cap["items"][0]["fields"]["rrn"] is None


def test_rrn_masked_never_leaks_when_off(client, monkeypatch):
    # ON 으로 주민번호를 심고 매칭에 걸리는 고객을 만든 뒤 OFF 로 되돌린다
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    client.post("/customers", json={"name": "마스킹유출", "phone": "010-9000-0001", "rrn": "900101-1234567"})
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")

    # GET /customers/match — match / candidates 어디에도 rrn_masked 없어야
    m = client.get("/customers/match", params={"phone": "010-9000-0001"}).json()
    assert m["match"]["rrn"] is None and m["match"]["rrn_masked"] is None
    assert m["match"]["has_rrn"] is True
    for c in m.get("candidates") or []:
        assert c["rrn_masked"] is None

    # /capture 미리보기의 item['match'] 도 마찬가지
    from database import intake

    monkeypatch.setattr(
        intake, "_call_llm",
        lambda *a, **k: {"customers": [{"name": "마스킹유출", "phone": "01090000001"}]},
    )
    cap = client.post("/capture", data={"text": "마스킹유출 01090000001"}).json()
    it = cap["items"][0]
    assert it["match"] is not None
    assert it["match"]["rrn"] is None and it["match"]["rrn_masked"] is None


def test_reveal_endpoints_404_when_off(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    cid = client.post("/customers", json={"name": "조회차단", "rrn": "900101-1234567"}).json()["id"]
    assert client.get(f"/customers/{cid}/rrn", params={"purpose": "확인"}).status_code == 200

    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    assert client.get(f"/customers/{cid}/rrn", params={"purpose": "확인"}).status_code == 404
    assert client.get(f"/customers/{cid}/rrn-access-log").status_code == 404
