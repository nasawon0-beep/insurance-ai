import base64
import json
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


def test_analysis_excludes_unconfirmed_and_unselected_policy_text_fallback(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    policy = client.post(f"/customers/{customer['id']}/policies", json={
        "product_name": "일반암 진단비 5,000만원 합성 참고문서",
        "document_status": "REFERENCE",
        "source_document_type": "product_description",
        "source_hash": "analysis-confirmation-source",
    }).json()
    rows_payload = [{
        "policy_id": policy["id"], "source_hash": "analysis-confirmation-source",
        "source_page": 4, "source_table": 1, "source_row": row,
        "rider_name": name, "standard_name": standard_name,
        "amount": amount, "amount_text": amount_text, "amount_status": "known",
        "document_status": "REFERENCE",
    } for row, name, standard_name, amount, amount_text in [
        (1, "일반암 진단비", "일반암 진단비", 50_000_000, "5,000만원"),
        (2, "합성 비암 선택담보", "기타", 1_000_000, "100만원"),
    ]]
    saved = client.post(f"/customers/{customer['id']}/policy-coverages/bulk", json={
        "review_confirmed": True, "items": rows_payload,
    })
    assert saved.status_code == 201, saved.text
    rows = saved.json()["items"]

    def cancer_item():
        recalculated = client.post(
            f"/customers/{customer['id']}/coverage-analysis/recalculate",
            json={"audience": "internal", "force": True},
        )
        assert recalculated.status_code == 200, recalculated.text
        body = client.get(
            f"/customers/{customer['id']}/coverage-analysis",
            params={"audience": "internal", "run_id": recalculated.json()["run_id"]},
        ).json()
        return next(
            item for category in body["categories"] for item in category["items"]
            if item["coverage_name"] == "일반암 진단비"
        )

    before = cancer_item()
    assert before["current_amount"] == 0
    assert policy["id"] not in before["matched_policy_ids"]

    cancer = rows[0]
    selected = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED",
        "confirmed_coverages": [{
            "id": cancer["id"], "source_page": cancer["source_page"],
            "source_table": cancer["source_table"], "source_row": cancer["source_row"],
        }],
    })
    assert selected.status_code == 200, selected.text
    after_selection = cancer_item()
    assert after_selection["current_amount"] == 50_000_000
    assert after_selection["matched_policy_ids"] == [policy["id"]]

    non_cancer = rows[1]
    partial = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "coverage_selection_mode": "REPLACE",
        "confirmed_coverages": [{
            "id": non_cancer["id"], "source_page": non_cancer["source_page"],
            "source_table": non_cancer["source_table"], "source_row": non_cancer["source_row"],
        }],
    })
    assert partial.status_code == 200, partial.text
    after_partial = cancer_item()
    assert after_partial["current_amount"] == 0
    assert policy["id"] not in after_partial["matched_policy_ids"]

    cancelled = client.patch(f"/policies/{policy['id']}", json={"document_status": "REFERENCE"})
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = cancer_item()
    assert after_cancel["current_amount"] == 0
    assert policy["id"] not in after_cancel["matched_policy_ids"]

    legacy_customer = client.post("/customers", json={"name": "합성기존고객"}).json()
    legacy_policy = client.post(f"/customers/{legacy_customer['id']}/policies", json={
        "product_name": "일반암 진단비 5,000만원 기존 확정계약",
    }).json()
    legacy_run = client.post(
        f"/customers/{legacy_customer['id']}/coverage-analysis/recalculate",
        json={"audience": "internal", "force": True},
    ).json()
    legacy_body = client.get(
        f"/customers/{legacy_customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": legacy_run["run_id"]},
    ).json()
    legacy_cancer = next(
        item for category in legacy_body["categories"] for item in category["items"]
        if item["coverage_name"] == "일반암 진단비"
    )
    assert legacy_cancer["current_amount"] == 50_000_000
    assert legacy_cancer["matched_policy_ids"] == [legacy_policy["id"]]


def test_observed_summary_is_preserved_separately_from_contract_calculation(client, monkeypatch):
    """원본 요약 0원은 원장 계산값으로 덮어쓰지 않고 차이를 함께 보존한다."""
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    saved = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [{
            "source_hash": "source-separation", "source_page": 31,
            "insurer": "테스트보험", "product_name": "테스트상품",
            "rider_name": "일상생활배상책임", "rider_name_raw": "일상생활배상책임",
            "standard_name": "일상생활 배상책임", "standard_name_raw": "일상생활배상",
            "amount": 100_000_000, "amount_text": "10,000만원", "amount_status": "known",
        }]},
    )
    assert saved.status_code == 201, saved.text

    recalculated = client.post(
        f"/customers/{customer['id']}/coverage-analysis/recalculate",
        json={"force": True, "observed_summary": [{
            "coverage_name": "일상생활 배상책임", "source_coverage_name": "일상생활 배상책임",
            "amount": 0, "amount_text": "0만원", "amount_status": "explicit_zero",
            "source_status": "미가입", "source_page": 6,
        }]},
    )
    assert recalculated.status_code == 200, recalculated.text
    assert recalculated.json()["observed_summary_count"] == 1
    response = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": recalculated.json()["run_id"]},
    )
    assert response.status_code == 200, response.text
    item = next(
        item for category in response.json()["categories"] for item in category["items"]
        if item["coverage_name"] == "일상생활 배상책임"
    )
    assert item["amount_source_kind"] == "observed_summary"
    assert item["current_amount"] == item["observed_current_amount"] == 0
    assert item["amount_status"] == item["observed_amount_status"] == "explicit_zero"
    assert item["observed_status"] == "미가입"
    assert item["observed_source_page"] == 6
    assert item["calculated_current_amount"] == 100_000_000
    assert item["calculated_amount_status"] == "known"
    assert item["source_difference"] == "원본 0원 / 자체계산 100000000원"
    assert item["matched_policy_coverage_ids"] == [saved.json()["items"][0]["id"]]


def test_explicit_other_rows_do_not_cross_link_by_broad_rider_keywords(client, monkeypatch):
    """기타로 명시된 중증화상/입원수술/자동차부상은 비슷한 표준담보로 합산하지 않는다."""
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    source_rows = [
        ("중증화상및부식진단비", 20_000_000),
        ("질병수술입원일당", 1_000_000),
        ("자동차사고부상보장(1-7급)", 30_000_000),
        ("10대특정암진단비", 10_000_000),
    ]
    saved = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={"review_confirmed": True, "items": [
            {"source_hash": "strict-link", "source_page": 32, "rider_name": name,
             "rider_name_raw": name, "standard_name": "기타", "standard_name_raw": "기타",
             "amount": amount, "amount_status": "known"}
            for name, amount in source_rows
        ]},
    )
    assert saved.status_code == 201, saved.text
    run = client.post(f"/customers/{customer['id']}/coverage-analysis/recalculate", json={"force": True})
    assert run.status_code == 200, run.text
    body = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": run.json()["run_id"]},
    ).json()
    items = {item["coverage_name"]: item for category in body["categories"] for item in category["items"]}
    assert items["화상 진단비"]["matched_policy_coverage_ids"] == []
    assert items["질병입원"]["matched_policy_coverage_ids"] == []
    assert items["질병수술비"]["matched_policy_coverage_ids"] == []
    assert items["자동차사고 부상치료비"]["matched_policy_coverage_ids"] == []
    assert items["통합암 진단비"]["matched_policy_coverage_ids"] == []
    assert {row["id"] for row in body["unlinked_policy_coverages"]} == {
        row["id"] for row in saved.json()["items"]
    }


def test_amount_semantics_and_unclassified_ledger_are_preserved_end_to_end(client, monkeypatch):
    """'-', 빈칸, 명시적 0원은 DB/API/분석에서 서로 다른 의미다."""
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    saved = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                {"source_hash": "amount-semantics", "source_page": 2, "insurer": "테스트보험", "product_name": "테스트상품", "rider_name": "뇌혈관 진단비", "standard_name": "뇌혈관 진단비", "amount": None, "amount_text": "-", "amount_status": "unknown_dash"},
                {"source_hash": "amount-semantics", "source_page": 2, "insurer": "테스트보험", "product_name": "테스트상품", "rider_name": "뇌출혈 진단비", "standard_name": "뇌출혈 진단비", "amount": None, "amount_text": "", "amount_status": "unknown_blank"},
                {"source_hash": "amount-semantics", "source_page": 2, "insurer": "테스트보험", "product_name": "테스트상품", "rider_name": "뇌졸중 진단비", "standard_name": "뇌졸중 진단비", "amount": 0, "amount_text": "0원", "amount_status": "explicit_zero"},
                {"source_hash": "amount-semantics", "source_page": 2, "insurer": "테스트보험", "product_name": "테스트상품", "rider_name": "원문 미분류 담보", "standard_name": "기타", "amount": None, "amount_text": "-", "amount_status": "unknown_dash"},
            ],
        },
    )
    assert saved.status_code == 201, saved.text

    ledger = client.get(f"/customers/{customer['id']}/policy-coverages").json()["policy_coverages"]
    by_name = {row["rider_name"]: row for row in ledger}
    assert by_name["뇌혈관 진단비"]["amount"] is None
    assert by_name["뇌혈관 진단비"]["amount_status"] == "unknown_dash"
    assert by_name["뇌출혈 진단비"]["amount"] is None
    assert by_name["뇌출혈 진단비"]["amount_status"] == "unknown_blank"
    assert by_name["뇌졸중 진단비"]["amount"] == 0
    assert by_name["뇌졸중 진단비"]["amount_status"] == "explicit_zero"

    recalculated = client.post(
        f"/customers/{customer['id']}/coverage-analysis/recalculate",
        json={"audience": "internal", "force": True},
    )
    assert recalculated.status_code == 200, recalculated.text
    detail = client.get(f"/customers/{customer['id']}/coverage-analysis", params={"audience": "internal"})
    assert detail.status_code == 200, detail.text
    body = detail.json()
    items = {item["coverage_name"]: item for category in body["categories"] for item in category["items"]}
    assert items["뇌혈관 진단비"]["status"] == "확인필요"
    assert items["뇌혈관 진단비"]["amount_status"] == "unknown"
    assert items["뇌출혈 진단비"]["status"] == "확인필요"
    assert items["뇌출혈 진단비"]["amount_status"] == "unknown"
    assert items["뇌졸중 진단비"]["status"] == "미가입"
    assert items["뇌졸중 진단비"]["amount_status"] == "explicit_zero"
    unclassified = body["unclassified_policy_coverages"]
    assert [row["id"] for row in unclassified] == [by_name["원문 미분류 담보"]["id"]]
    assert unclassified[0]["amount_status"] == "unknown_dash"


def test_link_refresh_requires_original_ledger_and_then_recalculates(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    missing = client.post(f"/customers/{customer['id']}/coverage-analysis/link-refresh", json={})
    assert missing.status_code == 409, missing.text
    assert "원본 담보" in missing.json()["detail"]

    saved = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                {
                    "source_hash": "link-refresh-source",
                    "source_page": 3,
                    "insurer": "테스트보험",
                    "product_name": "테스트상품",
                    "rider_name": "뇌혈관질환진단비",
                    "standard_name": "뇌혈관질환진단비",
                    "amount": 30_000_000,
                }
            ],
        },
    )
    assert saved.status_code == 201, saved.text
    refreshed = client.post(f"/customers/{customer['id']}/coverage-analysis/link-refresh", json={})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "completed"
    assert refreshed.json()["source_policy_coverages"] == 1
    # 고객 상세와 독립 화면은 이 동일 전용 endpoint 응답을 사용한다.
    customer_detail_snapshot = client.get(
        f"/customers/{customer['id']}/coverage-analysis", params={"audience": "internal"}
    ).json()
    independent_snapshot = client.get(
        f"/customers/{customer['id']}/coverage-analysis", params={"audience": "internal"}
    ).json()
    assert customer_detail_snapshot["run_id"] == independent_snapshot["run_id"] == refreshed.json()["run_id"]
    detail_a = {
        item["id"]: (item["current_amount"], item["amount_status"], item["matched_policy_coverage_ids"])
        for category in customer_detail_snapshot["categories"] for item in category["items"]
    }
    detail_b = {
        item["id"]: (item["current_amount"], item["amount_status"], item["matched_policy_coverage_ids"])
        for category in independent_snapshot["categories"] for item in category["items"]
    }
    assert detail_a == detail_b
    brain = next(
        item for category in customer_detail_snapshot["categories"] for item in category["items"]
        if item["coverage_name"] == "뇌혈관 진단비"
    )
    assert brain["matched_policy_coverage_ids"] == [saved.json()["items"][0]["id"]]


def test_link_refresh_uses_saved_pdf_summary_without_fabricating_contract_ledger(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    summary = [
        {"name": "뇌혈관 진단비", "current": "3,000만원", "source_page": 5},
        {"name": "뇌혈관 진단비", "current": "3,000만원", "source_page": 5},
        {"name": "뇌출혈 진단비", "current": "2,000만원", "source_page": 5},
        {"name": "뇌졸중 진단비", "current": "1,000만원", "source_page": 5},
    ]
    consultation = client.post(
        f"/customers/{customer['id']}/consultations",
        json={"title": "PDF 표준항목 집계", "coverage_json": json.dumps(summary, ensure_ascii=False)},
    )
    assert consultation.status_code == 201, consultation.text

    refreshed = client.post(f"/customers/{customer['id']}/coverage-analysis/link-refresh", json={})
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["source_kind"] == "observed_summary"
    assert body["source_policy_coverages"] == 0
    assert body["source_summary_items"] == 4
    assert client.get(f"/customers/{customer['id']}/policy-coverages").json()["policy_coverages"] == []

    detail = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": body["run_id"]},
    )
    items = {item["coverage_name"]: item for cat in detail.json()["categories"] for item in cat["items"]}
    assert items["뇌혈관 진단비"]["current_amount"] == 30_000_000
    assert items["뇌출혈 진단비"]["current_amount"] == 20_000_000
    assert items["뇌졸중 진단비"]["current_amount"] == 10_000_000
    assert items["뇌혈관 진단비"]["matched_policy_ids"] == []


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

    runs = client.get("/coverage-analysis-runs")
    assert runs.status_code == 200, runs.text
    run_customers = [run["customer"] for run in runs.json()["runs"]]
    assert any(c["id"] == customer["id"] and c["name"] == customer["name"] for c in run_customers)
    assert any(run["run_id"] == body["run_id"] and run["items_count"] == 50 for run in runs.json()["runs"])

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
    assert all("matched_policy_ids" in item for item in customer_items)
    assert all("matched_policy_coverage_ids" in item for item in customer_items)
    assert all("evidence" not in item for item in customer_items)


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
    assert daily["matched_policy_coverage_ids"] == [created.json()["items"][0]["id"]]


def test_policy_coverage_matching_persists_flexible_standard_names(client, monkeypatch):
    from database import coverage
    from database.db import resolve_db_path

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    policy_ids = []
    for index in range(3):
        response = client.post(
            f"/customers/{customer['id']}/policies",
            json={"insurer": f"테스트보험{index}", "product_name": f"건강보험{index}"},
        )
        assert response.status_code == 201, response.text
        policy_ids.append(response.json()["id"])

    created = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                {"policy_id": policy_ids[0], "standard_name": "뇌혈관질환 진단비", "amount": 30000000},
                {"policy_id": policy_ids[1], "standard_name": "뇌출혈 진단비", "amount": 20000000},
                {"policy_id": policy_ids[2], "standard_name": "뇌졸중 진단비", "amount": 10000000},
                {"policy_id": policy_ids[2], "standard_name": "기타", "rider_name": "통합포인트 대상 질병진단비((B8) 외상성뇌출혈진단비)", "amount": 100000},
                {"policy_id": policy_ids[0], "standard_name": "질병사망", "amount": 100000000},
                {"policy_id": policy_ids[0], "standard_name": "일반암 진단비", "amount": 50000000},
                {"policy_id": policy_ids[1], "standard_name": "상해사망", "amount": 100000000},
                {"policy_id": policy_ids[0], "standard_name": "상해+질병 입원의료비", "amount": 50000000},
                {"policy_id": policy_ids[2], "standard_name": "질병 외래의료비", "amount": 250000},
                {"policy_id": policy_ids[1], "standard_name": "상해 외래의료비", "amount": 250000},
            ],
        },
    )
    assert created.status_code == 201, created.text
    coverage_ids_by_name = {
        item["standard_name"]: item["id"] for item in created.json()["items"]
    }
    traumatic_brain_coverage_id = next(
        item["id"] for item in created.json()["items"]
        if "외상성뇌출혈" in (item.get("rider_name") or "")
    )

    recalculated = client.post(f"/customers/{customer['id']}/coverage-analysis/recalculate", json={})
    assert recalculated.status_code == 200, recalculated.text
    run_id = recalculated.json()["run_id"]
    response = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    assert response.status_code == 200, response.text
    items = {
        item["coverage_name"]: item
        for category in response.json()["categories"]
        for item in category["items"]
    }
    assert items["뇌혈관 진단비"]["matched_policy_ids"] == [policy_ids[0]]
    assert items["뇌출혈 진단비"]["matched_policy_ids"] == [policy_ids[1]]
    assert items["뇌졸중 진단비"]["matched_policy_ids"] == [policy_ids[2]]
    assert items["질병사망"]["matched_policy_ids"] == [policy_ids[0]]
    assert items["상해사망"]["matched_policy_ids"] == [policy_ids[1]]
    assert items["질병입원"]["matched_policy_ids"] == [policy_ids[0]]
    assert items["상해입원"]["matched_policy_ids"] == [policy_ids[0]]
    assert items["질병통원"]["matched_policy_ids"] == [policy_ids[2]]
    assert items["상해통원"]["matched_policy_ids"] == [policy_ids[1]]
    assert items["뇌혈관 진단비"]["current_amount"] == 30000000
    assert items["뇌출혈 진단비"]["current_amount"] == 20000000
    assert items["뇌졸중 진단비"]["current_amount"] == 10000000
    assert items["질병사망"]["current_amount"] == 100000000
    assert items["상해사망"]["current_amount"] == 100000000
    assert items["뇌혈관 진단비"]["matched_policy_coverage_ids"] == [coverage_ids_by_name["뇌혈관질환 진단비"]]
    assert items["뇌출혈 진단비"]["matched_policy_coverage_ids"] == [coverage_ids_by_name["뇌출혈 진단비"]]
    assert items["뇌졸중 진단비"]["matched_policy_coverage_ids"] == [coverage_ids_by_name["뇌졸중 진단비"]]
    assert traumatic_brain_coverage_id not in items["뇌혈관 진단비"]["matched_policy_coverage_ids"]
    assert items["질병사망"]["matched_policy_coverage_ids"] == [coverage_ids_by_name["질병사망"]]
    assert coverage_ids_by_name["일반암 진단비"] not in items["질병사망"]["matched_policy_coverage_ids"]
    assert items["상해사망"]["matched_policy_coverage_ids"] == [coverage_ids_by_name["상해사망"]]
    assert items["중증치매진단"]["matched_policy_ids"] == []
    assert items["중증치매진단"]["matched_policy_coverage_ids"] == []

    displayed = client.patch(
        f"/customers/{customer['id']}/coverage-analysis/{items['뇌혈관 진단비']['id']}",
        json={"customer_display": True, "override_reason": "고객 응답 연결 ID 테스트"},
    )
    assert displayed.status_code == 200, displayed.text

    customer_response = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "customer", "run_id": run_id},
    )
    assert customer_response.status_code == 200, customer_response.text
    customer_items = {
        item["coverage_name"]: item
        for category in customer_response.json()["categories"]
        for item in category["items"]
    }
    assert customer_items["뇌혈관 진단비"]["matched_policy_ids"] == [policy_ids[0]]
    assert len(customer_items["뇌혈관 진단비"]["matched_policy_coverage_ids"]) == 1

    conn = sqlite3.connect(resolve_db_path())
    try:
        stored = conn.execute(
            """
            SELECT matched_policy_ids_json FROM coverage_analysis
            WHERE run_id = ? AND coverage_name = '뇌혈관 진단비'
            """,
            (run_id,),
        ).fetchone()[0]
        assert json.loads(stored) == [policy_ids[0]]

        conn.execute(
            """
            UPDATE coverage_analysis SET matched_policy_ids_json = '[]'
            WHERE run_id = ? AND coverage_name = '뇌혈관 진단비'
            """,
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    refreshed = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    refreshed_items = {
        item["coverage_name"]: item
        for category in refreshed.json()["categories"]
        for item in category["items"]
    }
    # GET은 저장된 revision을 재분류하거나 DB에 다시 쓰지 않는다.
    assert refreshed_items["뇌혈관 진단비"]["matched_policy_ids"] == []


def test_saved_customer_coverage_and_independent_analysis_share_amounts_and_details(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *args, **kwargs: {"hits": []})
    customer = _make_customer(client)
    policy = client.post(
        f"/customers/{customer['id']}/policies",
        json={"insurer": "테스트보험", "product_name": "보유계약"},
    )
    assert policy.status_code == 201, policy.text
    policy_id = policy.json()["id"]

    coverage_rows = [
        {"name": "질병사망", "current": 11000000, "status": "부족"},
        {"name": "상해사망", "current": 371000000, "status": "충분"},
        {"name": "뇌혈관 진단비", "current": 30000000, "status": "부족"},
    ]
    consultation = client.post(
        f"/customers/{customer['id']}/consultations",
        json={"title": "저장된 보장분석", "coverage_json": json.dumps(coverage_rows, ensure_ascii=False)},
    )
    assert consultation.status_code == 201, consultation.text

    saved = client.post(
        f"/customers/{customer['id']}/policy-coverages/bulk",
        json={
            "review_confirmed": True,
            "items": [
                {"policy_id": policy_id, "standard_name": "질병사망", "rider_name": "질병사망 특약", "amount": 11000000},
                {"policy_id": policy_id, "standard_name": "상해사망", "rider_name": "상해사망 특약", "amount": 371000000},
                {"policy_id": policy_id, "standard_name": "뇌혈관질환 진단비", "rider_name": "뇌혈관질환 진단 특약", "amount": 30000000},
                {"policy_id": policy_id, "standard_name": "일반암 진단비", "rider_name": "일반암 진단 특약", "amount": 50000000},
            ],
        },
    )
    assert saved.status_code == 201, saved.text
    coverage_ids = {row["standard_name"]: row["id"] for row in saved.json()["items"]}

    recalculated = client.post(f"/customers/{customer['id']}/coverage-analysis/recalculate", json={})
    assert recalculated.status_code == 200, recalculated.text
    run_id = recalculated.json()["run_id"]

    detail = client.get(f"/customers/{customer['id']}")
    assert detail.status_code == 200, detail.text
    detail_rows = json.loads(detail.json()["consultations"][0]["coverage_json"])
    detail_amounts = {row["name"]: row["current"] for row in detail_rows}

    analysis = client.get(
        f"/customers/{customer['id']}/coverage-analysis",
        params={"audience": "internal", "run_id": run_id},
    )
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["customer_id"] == customer["id"]
    assert analysis.json()["run_id"] == run_id
    analysis_items = {
        item["coverage_name"]: item
        for category in analysis.json()["categories"]
        for item in category["items"]
    }
    for name in ("질병사망", "상해사망", "뇌혈관 진단비"):
        assert analysis_items[name]["current_amount"] == detail_amounts[name]
        assert analysis_items[name]["status"] != "미가입"

    assert analysis_items["질병사망"]["matched_policy_coverage_ids"] == [coverage_ids["질병사망"]]
    assert analysis_items["상해사망"]["matched_policy_coverage_ids"] == [coverage_ids["상해사망"]]
    assert analysis_items["뇌혈관 진단비"]["matched_policy_coverage_ids"] == [coverage_ids["뇌혈관질환 진단비"]]
    assert coverage_ids["일반암 진단비"] not in analysis_items["질병사망"]["matched_policy_coverage_ids"]


def test_save_coverage_analysis_skips_proposal_riders(client):
    customer = _make_customer(client)

    saved = client.post(
        "/coverage-analysis",
        json={
            "customer_id": customer["id"],
            "doc_type": "proposal",
            "review_confirmed": True,
            "riders": [{"rider_name": "일반암진단비", "amount_text": "3,000만원"}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "proposal_reviewed"
    assert saved.json()["saved"] is False

    coverages = client.get(f"/customers/{customer['id']}/policy-coverages")
    assert coverages.status_code == 200, coverages.text
    assert coverages.json()["policy_coverages"] == []


def test_save_coverage_analysis_persists_only_coverage_analysis(client):
    customer = _make_customer(client)

    saved = client.post(
        "/coverage-analysis",
        json={
            "customer_id": customer["id"],
            "doc_type": "coverage_analysis",
            "review_confirmed": True,
            "coverages": [
                {
                    "insurer": "테스트보험",
                    "product_name": "보장분석 요약",
                    "coverage_name": "질병 입원일당",
                    "amount": 30000,
                    "amount_text": "3만원",
                    "source_page": 2,
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "saved"
    assert saved.json()["count"] == 1

    coverages = client.get(f"/customers/{customer['id']}/policy-coverages")
    assert coverages.status_code == 200, coverages.text
    rows = coverages.json()["policy_coverages"]
    assert len(rows) == 1
    assert rows[0]["rider_name"] == "질병 입원일당"
