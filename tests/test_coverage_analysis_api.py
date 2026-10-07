import base64
import sqlite3

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()

    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def _make_customer(client):
    r = client.post("/customers", json={"name": "김철수", "birth_date": "1976-05-01", "gender": "남"})
    assert r.status_code == 201, r.text
    return r.json()


def test_recalculate_persists_50_items_and_filters_customer_response(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    policy = client.post(
        f"/customers/{customer['id']}/policies",
        json={
            "insurer": "테스트보험",
            "product_name": "건강보험 일반암 진단비 3,000만원",
            "memo": "일반암 진단비 3,000만원, 질병입원 5,000만원",
        },
    )
    assert policy.status_code == 201, policy.text

    recalculated = client.post(
        f"/customers/{customer['id']}/coverage-analysis/recalculate",
        json={"profile": {"age": 50, "sex": "male", "has_dependents": True}, "persist": True},
    )
    assert recalculated.status_code == 200, recalculated.text
    body = recalculated.json()
    assert body["status"] == "completed"
    assert body["items_count"] == 50

    internal = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": body["run_id"]},
    )
    assert internal.status_code == 200, internal.text
    internal_body = internal.json()
    internal_items = [item for cat in internal_body["categories"] for item in cat["items"]]
    assert len(internal_items) == 50
    assert len(internal_body["categories"]) == 13
    assert any(item["priority"] == "최우선" for item in internal_items)
    assert any(item["priority"] == "중요" for item in internal_items)
    assert any(item["priority"] == "선택" for item in internal_items)
    assert "internal_memo" in internal_items[0]

    first = internal_items[0]
    patched = client.patch(
        f"/customers/{customer['id']}/coverage-analysis/{first['id']}",
        json={"customer_display": False, "internal_memo": "내부 검토 메모", "override_reason": "테스트"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["manual_override"] is True
    assert patched.json()["internal_memo"] == "내부 검토 메모"

    customer_resp = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "customer", "run_id": body["run_id"]},
    )
    assert customer_resp.status_code == 200, customer_resp.text
    customer_body = customer_resp.json()
    customer_items = [item for cat in customer_body["categories"] for item in cat["items"]]
    assert first["id"] not in {item["id"] for item in customer_items}
    assert len(customer_items) < len(internal_items)
    assert all(item["customer_display"] for item in customer_items)
    assert all("internal_memo" not in item for item in customer_items)
    assert all("matched_policy_ids" not in item for item in customer_items)


def test_coverage_schema_tables_are_created(client):
    customer = _make_customer(client)
    r = client.post(f"/customers/{customer['id']}/coverage-analysis/recalculate", json={})
    assert r.status_code == 200, r.text

    from database.db import resolve_db_path

    conn = sqlite3.connect(resolve_db_path())
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "coverage_catalog",
                "coverage_analysis_runs",
                "coverage_analysis",
                "coverage_adjustment_factors",
            )
        }
    finally:
        conn.close()
    assert counts["coverage_catalog"] == 50
    assert counts["coverage_analysis_runs"] == 1
    assert counts["coverage_analysis"] == 50
    assert counts["coverage_adjustment_factors"] >= 0


def test_recalculate_uses_policy_coverages_for_hospitalization_daily_amount(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    policy = client.post(
        f"/customers/{customer['id']}/policies",
        json={"insurer": "테스트보험", "product_name": "건강보험"},
    )
    assert policy.status_code == 201, policy.text
    pid = policy.json()["id"]

    created = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                {
                    "policy_id": pid,
                    "standard_name": "질병입원일당",
                    "rider_name": "질병 입원일당(1일이상)",
                    "amount": 30000,
                    "amount_text": "3만원",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text

    recalculated = client.post(f"/customers/{customer['id']}/coverage-analysis/recalculate", json={})
    assert recalculated.status_code == 200, recalculated.text
    run_id = recalculated.json()["run_id"]
    internal = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    assert internal.status_code == 200, internal.text
    items = [item for cat in internal.json()["categories"] for item in cat["items"]]
    daily = next(item for item in items if item["coverage_name"] == "질병일당")
    assert daily["current_amount"] == 30000
    assert daily["status"] != "미가입"
    assert daily["matched_policy_ids"] == [pid]
