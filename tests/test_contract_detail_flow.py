from database import coverage, policy_coverages, repo
from database.db import connect, init_schema
from database.proposal_parser import _extract_riders_from_page


class _TablePage:
    def extract_tables(self):
        return [[
            ["구분", "특약명", "가입금액", "특약보험료", "보험기간", "납입기간", "갱신"],
            ["기본계약", "합성 주계약", "1,000만원", "12,300원", "100세", "20년", "비갱신"],
            ["특약", "원문 미분류 담보", "-", "", "10년", "전기납", "갱신"],
        ]]


class _ReferencePage:
    def extract_tables(self):
        return [[
            ["보장내용", "가입금액"],
            [None, None],
            ["합성 참고 설명", None],
        ]]


def test_contract_table_adapter_preserves_null_fields_and_source_coordinates():
    rows = _extract_riders_from_page(_TablePage(), source_page=7)
    assert len(rows) == 2
    assert rows[0].premium == 12_300
    assert rows[0].premium_text == "12,300원"
    assert rows[0].renewal_status == "비갱신"
    assert (rows[0].source_page, rows[0].source_table, rows[0].source_row) == (7, 0, 1)
    assert rows[1].coverage_amount is None
    assert rows[1].coverage_amount_text == "-"
    assert rows[1].renewal_status == "갱신"


def test_reference_adapter_does_not_turn_empty_pdf_cells_into_none_rows():
    from database.proposal_parser import _extract_reference_rows_from_page

    rows = _extract_reference_rows_from_page(_ReferencePage(), 3, set())
    assert [(row.rider_name, row.source_row) for row in rows] == [
        ("보장내용", 0), ("합성 참고 설명", 2),
    ]


def test_proposed_contract_roundtrip_is_idempotent_and_excluded_from_holdings(tmp_path, monkeypatch):
    db_path = tmp_path / "contract-detail.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "비식별고객"})
    policy = repo.create_policy(conn, customer["id"], {
        "insurer": "테스트보험",
        "product_name": "테스트 제안상품",
        "premium": 12300,
        "status": "ACTIVE",
        "document_status": "PROPOSED",
        "source_hash": "synthetic-proposal-hash",
        "source_document_type": "proposal",
        "is_own": False,
    })
    item = {
        "policy_id": policy["id"],
        "source_hash": "synthetic-proposal-hash",
        "source_page": 7,
        "source_table": 0,
        "source_row": 1,
        "insurer": "테스트보험",
        "product_name": "테스트 제안상품",
        "rider_name": "원문 미분류 담보",
        "standard_name": "기타",
        "amount": None,
        "amount_text": "-",
        "amount_status": "unknown_dash",
        "rider_premium": None,
        "rider_premium_text": None,
        "coverage_period_text": "10년",
        "payment_period_text": "전기납",
        "renewal_status": "갱신",
        "document_status": "PROPOSED",
    }
    first = policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    second = policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_existing"] == 1

    reopened = repo.get_customer(conn, customer["id"])
    reopened["policies"] = repo.list_policies(conn, customer["id"])
    reopened = policy_coverages.attach_coverages_to_policies(conn, reopened)
    saved_policy = next(p for p in reopened["policies"] if p["id"] == policy["id"])
    assert saved_policy["document_status"] == "PROPOSED"
    assert saved_policy["coverage_count"] == 1
    saved = saved_policy["coverages"][0]
    assert saved["source_table"] == 0 and saved["source_row"] == 1
    assert saved["coverage_period_text"] == "10년"
    assert saved["payment_period_text"] == "전기납"
    assert saved["renewal_status"] == "갱신"

    assert repo.get_customer(conn, customer["id"])["customer_status"] != "가입"
    assert repo.dashboard_counts(conn)["policies"] == 0
    assert coverage._list_customer_policy_coverages(conn, customer["id"]) == []

    try:
        repo.update_policy(conn, policy["id"], {"document_status": "CONFIRMED"})
        assert False, "bare contract-level confirmation must be rejected"
    except ValueError:
        pass
    assert coverage._list_customer_policy_coverages(conn, customer["id"]) == []

    repo.update_policy(conn, policy["id"], {
        "document_status": "CONFIRMED",
        "confirmed_coverages": [{
            "id": saved["id"], "source_page": 7, "source_table": 0, "source_row": 1,
        }],
    })
    confirmed_rows = coverage._list_customer_policy_coverages(conn, customer["id"])
    assert len(confirmed_rows) == 1
    assert repo.get_customer(conn, customer["id"])["customer_status"] == "가입"


def test_legacy_schema_migrates_existing_contracts_to_confirmed(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "legacy"})
    policy = repo.create_policy(conn, customer["id"], {"product_name": "legacy product"})
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(policies)")}
    if "document_status" in columns:
        conn.execute("ALTER TABLE policies DROP COLUMN document_status")
        conn.commit()
    conn.close()

    migrated = connect(db_path)
    init_schema(migrated)
    row = migrated.execute("SELECT document_status FROM policies WHERE id=?", (policy["id"],)).fetchone()
    assert row["document_status"] == "CONFIRMED"


def test_api_confirms_only_selected_evidenced_coverages_and_preserves_decision(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from main import app

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "selection-api.sqlite3"))
    client = TestClient(app)
    secret = getattr(app.state, "api_secret", None)
    header = getattr(app.state, "api_secret_header", None)
    if secret and header:
        client.headers[header] = secret
    first_customer = client.post("/customers", json={"name": "합성고객A"}).json()
    other_customer = client.post("/customers", json={"name": "합성고객B"}).json()
    policy = client.post(f"/customers/{first_customer['id']}/policies", json={
        "product_name": "합성 참고문서", "document_status": "REFERENCE",
        "source_document_type": "product_description", "source_hash": "synthetic-source-a",
    }).json()
    other_policy = client.post(f"/customers/{other_customer['id']}/policies", json={
        "product_name": "다른 고객 참고문서", "document_status": "REFERENCE",
        "source_document_type": "product_description", "source_hash": "synthetic-source-b",
    }).json()

    def item(pid, source_hash, row, name):
        return {
            "policy_id": pid, "source_hash": source_hash, "source_page": 4,
            "source_table": 1, "source_row": row, "rider_name": name,
            "standard_name": "기타", "amount": 10_000_000 + row,
            "amount_text": "합성금액", "document_status": "REFERENCE",
        }

    original_items = [
        item(policy["id"], "synthetic-source-a", 1, "실제가입선택대상"),
        item(policy["id"], "synthetic-source-a", 2, "설명예시행"),
    ]
    assert client.post(f"/customers/{first_customer['id']}/policy-coverages/bulk", json={
        "review_confirmed": True, "items": original_items,
    }).status_code == 201
    assert client.post(f"/customers/{other_customer['id']}/policy-coverages/bulk", json={
        "review_confirmed": True,
        "items": [item(other_policy["id"], "synthetic-source-b", 1, "다른고객행")],
    }).status_code == 201

    rows = client.get(
        f"/customers/{first_customer['id']}/policy-coverages", params={"policy_id": policy["id"]},
    ).json()["policy_coverages"]
    other_row = client.get(
        f"/customers/{other_customer['id']}/policy-coverages", params={"policy_id": other_policy["id"]},
    ).json()["policy_coverages"][0]
    assert client.patch(f"/policies/{policy['id']}", json={"document_status": "CONFIRMED"}).status_code == 409
    assert client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "confirmed_coverages": [{
            "id": other_row["id"], "source_page": 4, "source_table": 1, "source_row": 1,
        }],
    }).status_code == 409

    selected = rows[0]
    confirmed = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "confirmed_coverages": [{
            "id": selected["id"], "source_page": selected["source_page"],
            "source_table": selected["source_table"], "source_row": selected["source_row"],
        }],
    })
    assert confirmed.status_code == 200
    reopened = client.get(
        f"/customers/{first_customer['id']}/policy-coverages", params={"policy_id": policy["id"]},
    ).json()["policy_coverages"]
    assert [row["document_status"] for row in reopened].count("CONFIRMED") == 1
    assert [row["document_status"] for row in reopened].count("REFERENCE") == 1

    second = reopened[1]
    ambiguous_add = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "confirmed_coverages": [{
            "id": second["id"], "source_page": second["source_page"],
            "source_table": second["source_table"], "source_row": second["source_row"],
        }],
    })
    assert ambiguous_add.status_code == 409

    replace_both = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "coverage_selection_mode": "REPLACE",
        "confirmed_coverages": [{
            "id": row["id"], "source_page": row["source_page"],
            "source_table": row["source_table"], "source_row": row["source_row"],
        } for row in reopened],
    })
    assert replace_both.status_code == 200
    after_add = client.get(
        f"/customers/{first_customer['id']}/policy-coverages", params={"policy_id": policy["id"]},
    ).json()["policy_coverages"]
    assert [row["document_status"] for row in after_add] == ["CONFIRMED", "CONFIRMED"]

    replace_second = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "coverage_selection_mode": "REPLACE",
        "confirmed_coverages": [{
            "id": second["id"], "source_page": second["source_page"],
            "source_table": second["source_table"], "source_row": second["source_row"],
        }],
    })
    assert replace_second.status_code == 200
    after_replace = client.get(
        f"/customers/{first_customer['id']}/policy-coverages", params={"policy_id": policy["id"]},
    ).json()["policy_coverages"]
    assert [row["document_status"] for row in after_replace] == ["REFERENCE", "CONFIRMED"]

    empty_selection = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "coverage_selection_mode": "REPLACE",
        "confirmed_coverages": [],
    })
    assert empty_selection.status_code == 409

    wrong_coordinate = client.patch(f"/policies/{policy['id']}", json={
        "document_status": "CONFIRMED", "coverage_selection_mode": "REPLACE",
        "confirmed_coverages": [{
            "id": selected["id"], "source_page": selected["source_page"],
            "source_table": selected["source_table"], "source_row": 999,
        }],
    })
    assert wrong_coordinate.status_code == 409

    duplicate = client.post(f"/customers/{first_customer['id']}/policy-coverages/bulk", json={
        "review_confirmed": True, "items": original_items,
    }).json()
    assert duplicate["created"] == 0 and duplicate["skipped_existing"] == 2
    after_duplicate = client.get(
        f"/customers/{first_customer['id']}/policy-coverages", params={"policy_id": policy["id"]},
    ).json()["policy_coverages"]
    assert [(row["id"], row["document_status"]) for row in after_duplicate] == [
        (row["id"], row["document_status"]) for row in after_replace
    ]
