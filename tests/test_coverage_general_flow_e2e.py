import base64

import pytest
from pdf_fixture_resolver import private_pdf

pytest.importorskip("httpx")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto
    from database import coverage

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "coverage-general-e2e.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", base64.b64encode(b"g" * 32).decode("ascii"))
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()
    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def test_new_customer_save_retry_list_select_detail_and_customer_isolation(client):
    a = client.post("/customers", json={"name": "신규고객A"})
    b = client.post("/customers", json={"name": "신규고객B"})
    assert a.status_code == 201 and b.status_code == 201
    aid, bid = a.json()["id"], b.json()["id"]

    policy = client.post(
        f"/customers/{aid}/policies",
        json={"insurer": "로컬보험", "product_name": "원본상품", "start_date": "2024-01-01"},
    )
    assert policy.status_code == 201, policy.text
    pid = policy.json()["id"]
    payload = {
        "review_confirmed": True,
        "items": [
            {
                "policy_id": pid,
                "source_hash": "local-pdf-sha256",
                "source_page": 6,
                "insurer": "로컬보험",
                "product_name": "원본상품",
                "rider_no": "1",
                "rider_name": "뇌혈관질환진단비",
                "standard_name": "뇌혈관질환 진단비",
                "amount": 30_000_000,
            }
        ],
    }
    first = client.post(f"/customers/{aid}/policy-coverages/bulk", json=payload)
    retry = client.post(f"/customers/{aid}/policy-coverages/bulk", json=payload)
    assert first.status_code == 201 and first.json()["created"] == 1
    assert retry.status_code == 201 and retry.json()["created"] == 0
    assert retry.json()["skipped_existing"] == 1

    cross_customer = client.post(
        f"/customers/{bid}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [{**payload["items"][0], "source_hash": "other", "policy_id": pid}]},
    )
    assert cross_customer.status_code == 409, cross_customer.text
    assert client.get(f"/customers/{bid}/policy-coverages").json()["policy_coverages"] == []

    linked = client.post(f"/customers/{aid}/coverage-analysis/link-refresh", json={})
    assert linked.status_code == 200, linked.text
    assert linked.json()["source_policy_coverages"] == 1
    run_id = linked.json()["run_id"]

    runs = client.get("/coverage-analysis-runs")
    assert runs.status_code == 200, runs.text
    selected = next(run for run in runs.json()["runs"] if run["run_id"] == run_id)
    assert selected["customer"]["id"] == aid

    detail = client.get(
        f"/customers/{aid}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    assert detail.status_code == 200, detail.text
    items = {item["coverage_name"]: item for cat in detail.json()["categories"] for item in cat["items"]}
    assert items["뇌혈관 진단비"]["current_amount"] == 30_000_000
    assert items["뇌혈관 진단비"]["matched_policy_ids"] == [pid]
    assert len(items["뇌혈관 진단비"]["matched_policy_coverage_ids"]) == 1


def test_real_pdf_capture_save_recalculate_list_select_detail(client):
    real_coverage_pdf = private_pdf("coverage_case_02")
    with real_coverage_pdf.open("rb") as stream:
        captured = client.post(
            "/capture",
            files={"file": ("private-coverage-analysis.pdf", stream, "application/pdf")},
        )
    assert captured.status_code == 200, captured.text
    item = captured.json()["items"][0]
    assert item["doc_type"] in {"보장분석", "coverage_analysis", "coverage_analysis_full", "coverage_analysis_short"}
    ledger = item["policy_coverages"]
    assert ledger
    assert all(row.get("insurer") not in {"통합보장분석", "알 수 없음"} for row in ledger)

    fields = item.get("fields") or {}
    created = client.post("/customers", json={"name": fields.get("name") or "실PDF신규고객"})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    if item.get("consultation"):
        consultation = client.post(f"/customers/{cid}/consultations", json=item["consultation"])
        assert consultation.status_code == 201, consultation.text
    for policy in item.get("policies") or []:
        if not (policy.get("insurer") or policy.get("product_name")):
            continue
        response = client.post(
            f"/customers/{cid}/policies",
            json={
                "insurer": policy.get("insurer"),
                "product_name": policy.get("product_name"),
                "start_date": policy.get("issued_date"),
                "status": "ACTIVE",
            },
        )
        assert response.status_code == 201, response.text

    observed_summary = item.get("coverage_status") or []
    assert len(observed_summary) == 50
    assert all(row.get("source_page") for row in observed_summary)
    payload = {
        "review_confirmed": True,
        "items": ledger,
        "source_occurrences": item.get("source_link_occurrences") or [],
    }
    saved = client.post(f"/customers/{cid}/policy-coverages/bulk", json=payload)
    assert saved.status_code == 201, saved.text
    assert saved.json()["created"] == len(ledger)
    retry = client.post(f"/customers/{cid}/policy-coverages/bulk", json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["created"] == 0
    assert retry.json()["skipped_existing"] == len(ledger)

    calculated = client.post(
        f"/customers/{cid}/coverage-analysis/recalculate",
        json={
            "audience": "customer",
            "force": True,
            "source": "capture_pdf",
            "source_hash": item.get("source_hash"),
            "observed_summary": observed_summary,
        },
    )
    assert calculated.status_code == 200, calculated.text
    run_id = calculated.json()["run_id"]
    runs = client.get("/coverage-analysis-runs").json()["runs"]
    assert any(run["run_id"] == run_id and run["customer"]["id"] == cid for run in runs)
    detail = client.get(
        f"/customers/{cid}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    assert detail.status_code == 200, detail.text
    rows = {row["coverage_name"]: row for cat in detail.json()["categories"] for row in cat["items"]}
    assert len(rows) == 50
    assert all(row["amount_source_kind"] == "observed_summary" for row in rows.values())
    source_rows = {row["coverage_name"]: row for row in observed_summary}
    assert {
        name: (row["current_amount"], row["observed_source_page"])
        for name, row in rows.items()
    } == {
        name: (row["current_amount"], row["source_page"])
        for name, row in source_rows.items()
    }
    assert len(client.get(f"/customers/{cid}/policy-coverages").json()["policy_coverages"]) == len(ledger)

    refreshed = client.post(
        f"/customers/{cid}/coverage-analysis/link-refresh",
        json={"audience": "internal", "run_id": run_id},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["source_summary_items"] == 50
    refreshed_detail = client.get(
        f"/customers/{cid}/coverage-analysis",
        params={"audience": "internal", "run_id": refreshed.json()["run_id"]},
    ).json()
    refreshed_rows = {row["coverage_name"]: row for cat in refreshed_detail["categories"] for row in cat["items"]}
    assert len(refreshed_rows) == 50
    assert all(row["amount_source_kind"] == "observed_summary" for row in refreshed_rows.values())
    assert {
        name: (row["current_amount"], row["amount_status"], row["observed_source_page"])
        for name, row in refreshed_rows.items()
    } == {
        name: (row["current_amount"], row["amount_status"], row["observed_source_page"])
        for name, row in rows.items()
    }
