import base64

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    monkeypatch.setenv("DEV_SKIP_AUTH", "1")
    crypto.reset_cache()

    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def test_main_recalculate_endpoint_calls_coverage_analyze_and_returns_run_link(client, monkeypatch):
    from database import coverage

    created = client.post("/customers", json={"name": "재분석고객"})
    assert created.status_code == 201, created.text
    customer_id = created.json()["id"]

    calls = []

    def fake_analyze(conn, cid, **kwargs):
        calls.append((cid, kwargs))
        return {"categories": [{"name": "암"}, {"name": "입원"}], "run_id": "run-from-analyze"}

    monkeypatch.setattr(coverage, "analyze", fake_analyze)

    response = client.post(f"/customers/{customer_id}/coverage-analysis/recalculate")

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls and calls[0][0] == customer_id
    assert body["run_id"].startswith("run-")
    assert body["customer_id"] == customer_id
    assert body["status"] == "completed"
    assert body["mode"] == "full"
    assert body["items_count"] == 2
    assert body["links"] == {
        "result": f"/customers/{customer_id}/coverage-analysis?run_id={body['run_id']}"
    }
