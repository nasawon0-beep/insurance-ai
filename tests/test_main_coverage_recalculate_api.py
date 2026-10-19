import base64
import json

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


def _ledger(source_key: str, source_hash: str, rider: str) -> dict:
    return {
        "source_hash": source_hash,
        "source_file_name": f"{source_hash}.pdf",
        "source_page": 2,
        "source_occurrence_key": source_key,
        "insurer": "안전보험",
        "product_name": "안전상품",
        "rider_name": rider,
        "rider_name_raw": rider,
        "standard_name": "암 진단비",
        "standard_name_raw": "암진단",
        "amount": 10_000_000,
        "amount_text": "1,000만원",
        "amount_status": "known",
        "start_date": "2025-01-01",
        "end_date": "2035-01-01",
    }


def _observed_payload(body: dict) -> list[tuple]:
    return sorted(
        (
            item["coverage_name"], item.get("observed_current_amount"), item.get("observed_amount_text"),
            item.get("observed_amount_status"), item.get("observed_status"), item.get("observed_source_page"),
        )
        for category in body.get("categories", [])
        for item in category.get("items", [])
        if item.get("amount_source_kind") == "observed_summary"
    )


def test_link_refresh_preserves_persisted_observed_summary_from_requested_revision(client):
    customer_id = client.post("/customers", json={"name": "원본불변고객"}).json()["id"]
    saved = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [_ledger("ledger:summary:2:0:1", "summary-doc", "요약담보")]},
    )
    assert saved.status_code == 201, saved.text
    observed = [{
        "coverage_name": "암 진단비", "source_coverage_name": "암진단", "amount": 10_000_000,
        "amount_text": "1,000만원", "amount_status": "known", "source_status": "부족", "source_page": 5,
    }]
    initial = client.post(
        f"/customers/{customer_id}/coverage-analysis/recalculate",
        json={"force": True, "source": "test_initial", "source_hash": "summary-doc", "observed_summary": observed},
    )
    assert initial.status_code == 200, initial.text
    initial_body = client.get(
        f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal", "run_id": initial.json()["run_id"]}
    ).json()
    baseline = _observed_payload(initial_body)
    assert len(baseline) == 1
    first = client.post(
        f"/customers/{customer_id}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": initial.json()["run_id"]},
    )
    assert first.status_code == 200, first.text
    first_body = client.get(
        f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal", "run_id": first.json()["run_id"]}
    ).json()
    second = client.post(
        f"/customers/{customer_id}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": first.json()["run_id"]},
    )
    assert second.status_code == 200, second.text
    second_body = client.get(
        f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal", "run_id": second.json()["run_id"]}
    ).json()
    assert _observed_payload(first_body) == baseline
    assert _observed_payload(second_body) == baseline
    assert first.json()["source_run_id"] == initial.json()["run_id"]


def test_occurrence_review_rejects_noncandidate_cross_document_and_candidate_zero_without_revision(client):
    customer_id = client.post("/customers", json={"name": "후보안전고객"}).json()["id"]
    rows = [
        _ledger("ledger:safe:2:0:1", "safe-doc", "후보"),
        _ledger("ledger:safe:2:0:2", "safe-doc", "동일문서비후보"),
        _ledger("ledger:other:2:0:3", "other-doc", "타문서비후보"),
    ]
    occurrences = [
        {
            "occurrence_key": "link:safe:8:0:1", "source_hash": "safe-doc", "source_page": 8,
            "source_table": 0, "source_row": 1, "source_standard_name_raw": "암진단",
            "source_standard_name": "암 진단비", "rider_name": "복수후보", "amount": 10_000_000,
            "amount_text": "1,000만원", "resolution_status": "unresolved", "resolution_reason": "candidate_multiple",
            "candidate_source_keys_json": json.dumps([rows[0]["source_occurrence_key"]]),
        },
        {
            "occurrence_key": "link:safe:8:0:2", "source_hash": "safe-doc", "source_page": 8,
            "source_table": 0, "source_row": 2, "source_standard_name_raw": "암진단",
            "source_standard_name": "암 진단비", "rider_name": "후보없음", "amount": None,
            "amount_text": "-", "resolution_status": "unresolved", "resolution_reason": "rider_name_mismatch",
            "candidate_source_keys_json": "[]",
        },
    ]
    saved = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": rows, "source_occurrences": occurrences},
    )
    assert saved.status_code == 201, saved.text
    observed = [{
        "coverage_name": "암 진단비", "source_coverage_name": "암진단", "amount": 10_000_000,
        "amount_text": "1,000만원", "amount_status": "known", "source_status": "부족", "source_page": 8,
    }]
    initial = client.post(
        f"/customers/{customer_id}/coverage-analysis/recalculate",
        json={"force": True, "source": "sequence_initial", "source_hash": "safe-doc", "observed_summary": observed},
    )
    baseline = _observed_payload(client.get(
        f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal", "run_id": initial.json()["run_id"]}
    ).json())
    assert len(baseline) == 1
    policy_before = client.get(f"/customers/{customer_id}/policy-coverages").json()["policy_coverages"]
    policies = {row["source_occurrence_key"]: row for row in policy_before}
    occurrence_rows = {
        row["occurrence_key"]: row
        for row in client.get(f"/customers/{customer_id}/coverage-source-occurrences").json()["source_link_occurrences"]
    }
    occurrence_id = occurrence_rows["link:safe:8:0:1"]["id"]
    for source_key in ("ledger:safe:2:0:2", "ledger:other:2:0:3"):
        rejected = client.post(
            f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
            json={"action": "link", "policy_coverage_id": policies[source_key]["id"]},
        )
        assert rejected.status_code == 409, rejected.text
        latest = client.get(f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal"})
        assert latest.json()["run_id"] == initial.json()["run_id"]
        current = client.get(f"/customers/{customer_id}/coverage-source-occurrences").json()["source_link_occurrences"]
        assert next(row for row in current if row["id"] == occurrence_id)["resolution_status"] == "unresolved"
        assert client.get(f"/customers/{customer_id}/policy-coverages").json()["policy_coverages"] == policy_before
    other_customer = client.post("/customers", json={"name": "다른고객"}).json()["id"]
    other_saved = client.post(
        f"/customers/{other_customer}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [_ledger("ledger:foreign:2:0:1", "safe-doc", "타고객")]},
    ).json()
    rejected_other = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
        json={"action": "link", "policy_coverage_id": other_saved["items"][0]["id"]},
    )
    assert rejected_other.status_code == 409
    assert client.get(f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal"}).json()["run_id"] == initial.json()["run_id"]
    assert client.get(f"/customers/{customer_id}/policy-coverages").json()["policy_coverages"] == policy_before
    zero_id = occurrence_rows["link:safe:8:0:2"]["id"]
    zero_link = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{zero_id}/review",
        json={"action": "link", "policy_coverage_id": policies["ledger:safe:2:0:1"]["id"]},
    )
    assert zero_link.status_code == 409
    assert client.get(f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal"}).json()["run_id"] == initial.json()["run_id"]
    assert client.get(f"/customers/{customer_id}/policy-coverages").json()["policy_coverages"] == policy_before
    held = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{zero_id}/review", json={"action": "hold"}
    )
    assert held.status_code == 200, held.text
    after_hold = client.get(f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal"}).json()
    assert _observed_payload(after_hold) == baseline
    assert len(after_hold["held_source_link_occurrences"]) == 1
    assert len(after_hold["unresolved_source_link_occurrences"]) == 1
    linked = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
        json={"action": "link", "policy_coverage_id": policies["ledger:safe:2:0:1"]["id"]},
    )
    assert linked.status_code == 200, linked.text
    after_link = client.get(f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal"}).json()
    assert _observed_payload(after_link) == baseline
    stale = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
        json={"action": "link", "policy_coverage_id": policies["ledger:safe:2:0:1"]["id"]},
    )
    assert stale.status_code == 409
    repeated = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": rows, "source_occurrences": occurrences},
    )
    assert repeated.status_code == 201, repeated.text
    refreshed = client.post(
        f"/customers/{customer_id}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": after_link["run_id"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    final_body = client.get(
        f"/customers/{customer_id}/coverage-analysis", params={"audience": "internal", "run_id": refreshed.json()["run_id"]}
    ).json()
    assert _observed_payload(final_body) == baseline
    final_occurrences = {
        row["occurrence_key"]: row["resolution_status"]
        for row in client.get(f"/customers/{customer_id}/coverage-source-occurrences").json()["source_link_occurrences"]
    }
    assert final_occurrences == {"link:safe:8:0:1": "confirmed", "link:safe:8:0:2": "held"}
    assert len(final_body["held_source_link_occurrences"]) == 1


def test_first_review_without_analysis_revision_returns_409_and_rolls_back(client):
    customer_id = client.post("/customers", json={"name": "최초검수고객"}).json()["id"]
    row = _ledger("ledger:fresh:2:0:1", "fresh-doc", "후보없음")
    occurrence = {
        "occurrence_key": "link:fresh:8:0:1", "source_hash": "fresh-doc", "source_page": 8,
        "source_table": 0, "source_row": 1, "source_standard_name_raw": "암진단",
        "source_standard_name": "암 진단비", "rider_name": "후보없음", "amount": None,
        "amount_text": "-", "resolution_status": "unresolved", "resolution_reason": "rider_name_mismatch",
        "candidate_source_keys_json": "[]",
    }
    saved = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [row], "source_occurrences": [occurrence]},
    )
    assert saved.status_code == 201, saved.text
    before_policy = client.get(f"/customers/{customer_id}/policy-coverages").json()
    before_occurrence = client.get(f"/customers/{customer_id}/coverage-source-occurrences").json()
    assert client.get(f"/customers/{customer_id}/coverage-analysis").status_code == 404
    occurrence_id = before_occurrence["source_link_occurrences"][0]["id"]
    rejected = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
        json={"action": "hold"},
    )
    assert rejected.status_code == 409, rejected.text
    assert client.get(f"/customers/{customer_id}/policy-coverages").json() == before_policy
    assert client.get(f"/customers/{customer_id}/coverage-source-occurrences").json() == before_occurrence
    assert client.get(f"/customers/{customer_id}/coverage-analysis").status_code == 404


def test_first_review_with_analysis_schema_but_no_completed_source_revision_returns_409(client):
    from database import coverage
    from database.db import connect

    customer_id = client.post("/customers", json={"name": "스키마만존재고객"}).json()["id"]
    row = _ledger("ledger:schema-only:2:0:1", "schema-only", "후보")
    occurrence = {
        "occurrence_key": "link:schema-only:8:0:1", "source_hash": "schema-only", "source_page": 8,
        "source_table": 0, "source_row": 1, "source_standard_name_raw": "암진단",
        "source_standard_name": "암 진단비", "rider_name": "검수행", "amount": 10_000_000,
        "amount_text": "1,000만원", "resolution_status": "unresolved", "resolution_reason": "candidate_multiple",
        "candidate_source_keys_json": json.dumps([row["source_occurrence_key"]]),
    }
    saved = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [row], "source_occurrences": [occurrence]},
    )
    assert saved.status_code == 201, saved.text
    conn = connect()
    coverage.ensure_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM coverage_analysis_runs").fetchone()[0] == 0
    before_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()
    before_policy = client.get(f"/customers/{customer_id}/policy-coverages").json()
    before_occurrence = client.get(f"/customers/{customer_id}/coverage-source-occurrences").json()
    occurrence_id = before_occurrence["source_link_occurrences"][0]["id"]
    target_id = before_policy["policy_coverages"][0]["id"]

    for payload in ({"action": "hold"}, {"action": "link", "policy_coverage_id": target_id}):
        rejected = client.post(
            f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
            json=payload,
        )
        assert rejected.status_code == 409, rejected.text
        assert client.get(f"/customers/{customer_id}/policy-coverages").json() == before_policy
        assert client.get(f"/customers/{customer_id}/coverage-source-occurrences").json() == before_occurrence

    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM coverage_analysis_runs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before_audit
    conn.close()


def test_occurrence_review_uses_observed_summary_from_same_source_document_not_latest_other_document(client):
    customer_id = client.post("/customers", json={"name": "문서요약격리고객"}).json()["id"]
    rows = [
        _ledger("ledger:source-a:2:0:1", "source-a", "A후보"),
        _ledger("ledger:source-b:2:0:1", "source-b", "B후보"),
    ]
    occurrence = {
        "occurrence_key": "link:source-a:8:0:1", "source_hash": "source-a", "source_page": 8,
        "source_table": 0, "source_row": 1, "source_standard_name_raw": "암진단",
        "source_standard_name": "암 진단비", "rider_name": "A검수", "amount": 10_000_000,
        "amount_text": "1,000만원", "resolution_status": "unresolved", "resolution_reason": "candidate_multiple",
        "candidate_source_keys_json": json.dumps([rows[0]["source_occurrence_key"]]),
    }
    saved = client.post(
        f"/customers/{customer_id}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": rows, "source_occurrences": [occurrence]},
    )
    assert saved.status_code == 201, saved.text
    source_a = client.post(
        f"/customers/{customer_id}/coverage-analysis/recalculate",
        json={"force": True, "source_hash": "source-a", "observed_summary": [{
            "coverage_name": "암 진단비", "source_coverage_name": "A원본", "amount": 10_000_000,
            "amount_text": "1,000만원", "amount_status": "known", "source_status": "부족", "source_page": 8,
        }]},
    )
    assert source_a.status_code == 200
    source_b = client.post(
        f"/customers/{customer_id}/coverage-analysis/recalculate",
        json={"force": True, "source_hash": "source-b", "observed_summary": [{
            "coverage_name": "암 진단비", "source_coverage_name": "B원본", "amount": 20_000_000,
            "amount_text": "2,000만원", "amount_status": "known", "source_status": "충분", "source_page": 9,
        }]},
    )
    assert source_b.status_code == 200
    policies = client.get(f"/customers/{customer_id}/policy-coverages").json()["policy_coverages"]
    target_id = next(row["id"] for row in policies if row["source_occurrence_key"] == "ledger:source-a:2:0:1")
    occurrence_id = client.get(
        f"/customers/{customer_id}/coverage-source-occurrences"
    ).json()["source_link_occurrences"][0]["id"]
    reviewed = client.post(
        f"/customers/{customer_id}/coverage-source-occurrences/{occurrence_id}/review",
        json={"action": "link", "policy_coverage_id": target_id},
    )
    assert reviewed.status_code == 200, reviewed.text
    body = client.get(
        f"/customers/{customer_id}/coverage-analysis",
        params={"audience": "internal", "run_id": reviewed.json()["run_id"]},
    ).json()
    observed = _observed_payload(body)
    assert len(observed) == 1
    assert observed[0][1:] == (10_000_000, "1,000만원", "known", "부족", 8)
