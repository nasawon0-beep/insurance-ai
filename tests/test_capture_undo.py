"""던져넣기 배치 stamp·조회·일괄 되돌리기 계약."""
import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"u" * 32).decode("ascii")
BATCH_A = "a" * 32
BATCH_B = "b" * 32


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    from database import crypto

    path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(path))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()
    yield path
    crypto.reset_cache()


@pytest.fixture
def client(db_path):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def _customer(client, name="고객", batch=None, rrn=None):
    headers = {"X-Import-Batch": batch} if batch else {}
    body = {"name": name}
    if rrn:
        body["rrn"] = rrn
    r = client.post("/customers", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _policy(client, cid, batch=None, product="계약"):
    headers = {"X-Import-Batch": batch} if batch else {}
    r = client.post(
        f"/customers/{cid}/policies",
        json={"insurer": "보험사", "product_name": product},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _consultation(client, cid, batch=None):
    headers = {"X-Import-Batch": batch} if batch else {}
    r = client.post(
        f"/customers/{cid}/consultations",
        json={"consulted_at": "2026-09-03", "title": "상담"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_undo_is_scoped_to_one_batch_and_preserves_unstamped_rows(client):
    a = _customer(client, "A", BATCH_A)
    b = _customer(client, "B", BATCH_B)
    plain = _customer(client, "기존")
    _policy(client, a["id"], BATCH_A)
    _consultation(client, a["id"], BATCH_A)

    r = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert r.status_code == 200
    assert r.json() == {
        "deleted": {"customers": 1, "policies": 1, "consultations": 1},
        "rrn_cleared": 0,
    }
    assert client.get(f"/customers/{a['id']}").status_code == 404
    assert client.get(f"/customers/{b['id']}").status_code == 200
    assert client.get(f"/customers/{plain['id']}").status_code == 200
    assert client.post(f"/capture/batches/{BATCH_A}/undo").status_code == 404


def test_merge_deletes_only_batch_children_and_reverts_derived_status(client):
    existing = _customer(client, "기존")
    old_policy = _policy(client, existing["id"], product="기존 계약")
    batch_policy = _policy(client, existing["id"], BATCH_A, "배치 계약")
    batch_consult = _consultation(client, existing["id"], BATCH_A)

    r = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert r.status_code == 200
    detail = client.get(f"/customers/{existing['id']}").json()
    assert [p["id"] for p in detail["policies"]] == [old_policy["id"]]
    assert batch_policy["id"] not in [p["id"] for p in detail["policies"]]
    assert batch_consult["id"] not in [k["id"] for k in detail["consultations"]]

    prospect = _customer(client, "가망")
    _policy(client, prospect["id"], BATCH_B, "유일 계약")
    assert client.get(f"/customers/{prospect['id']}").json()["customer_status"] == "가입"
    assert client.post(f"/capture/batches/{BATCH_B}/undo").status_code == 200
    assert client.get(f"/customers/{prospect['id']}").json()["customer_status"] == "가망"


def test_merge_new_rrn_is_cleared_but_original_rrn_is_preserved(client, monkeypatch, db_path):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    new_rrn = _customer(client, "주민번호 없음")
    original = _customer(client, "주민번호 있음", rrn="900101-1234567")

    added = client.patch(
        f"/customers/{new_rrn['id']}",
        json={"rrn": "900102-2234567"},
        headers={"X-Import-Batch": BATCH_A},
    )
    kept = client.patch(
        f"/customers/{original['id']}",
        json={"rrn": "900101-1234567"},
        headers={"X-Import-Batch": BATCH_A},
    )
    assert added.status_code == kept.status_code == 200
    with sqlite3.connect(db_path) as conn:
        targets = conn.execute(
            "SELECT customer_id FROM import_batch_rrn WHERE batch_id=?", (BATCH_A,)
        ).fetchall()
    assert targets == [(new_rrn["id"],)]

    r = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert r.status_code == 200 and r.json()["rrn_cleared"] == 1
    assert client.get(f"/customers/{new_rrn['id']}").json()["has_rrn"] is False
    assert client.get(f"/customers/{original['id']}").json()["rrn"] == "9001011234567"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT rrn, rrn_hash FROM customers WHERE id=?", (new_rrn["id"],)
        ).fetchone() == (None, None)


def test_undo_preserves_rrn_changed_after_batch(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    customer = _customer(client, "후속 교정")
    added = client.patch(
        f"/customers/{customer['id']}",
        json={"rrn": "900102-2234567"},
        headers={"X-Import-Batch": BATCH_A},
    )
    assert added.status_code == 200
    corrected = client.patch(
        f"/customers/{customer['id']}", json={"rrn": "900103-1234567"}
    )
    assert corrected.status_code == 200
    _consultation(client, customer["id"], BATCH_A)

    undone = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert undone.status_code == 200
    assert undone.json()["rrn_cleared"] == 0
    assert client.get(f"/customers/{customer['id']}").json()["rrn"] == "9001031234567"


def test_expired_rrn_only_batch_returns_410_and_preserves_rrn(client, monkeypatch, db_path):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    customer = _customer(client, "오래된 주민번호")
    assert client.patch(
        f"/customers/{customer['id']}",
        json={"rrn": "900102-2234567"},
        headers={"X-Import-Batch": BATCH_A},
    ).status_code == 200
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE import_batch_rrn SET created_at=? WHERE batch_id=?", (old, BATCH_A))
        conn.commit()

    undone = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert undone.status_code == 410
    assert client.get(f"/customers/{customer['id']}").json()["rrn"] == "9001022234567"


def test_rrn_only_batch_is_listed_with_zero_counts(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    customer = _customer(client, "주민번호 배치")
    assert client.patch(
        f"/customers/{customer['id']}",
        json={"rrn": "900102-2234567"},
        headers={"X-Import-Batch": BATCH_A},
    ).status_code == 200

    batches = client.get("/capture/batches", params={"limit": 5}).json()["batches"]
    item = next(batch for batch in batches if batch["id"] == BATCH_A)
    assert item["counts"] == {"customers": 0, "policies": 0, "consultations": 0}
    assert item["undoable"] is True


def test_partial_stamp_invalid_headers_and_batch_id_validation(client, db_path):
    stamped = _customer(client, "부분", BATCH_A)
    bad = _customer(client, "불량", "not-a-batch")
    plain = _customer(client, "헤더 없음")
    assert client.post("/capture/batches/not-a-batch/undo").status_code == 422
    assert client.post(f"/capture/batches/{BATCH_A}/undo").json()["deleted"]["customers"] == 1
    assert client.get(f"/customers/{stamped['id']}").status_code == 404
    assert client.get(f"/customers/{bad['id']}").status_code == 200
    assert client.get(f"/customers/{plain['id']}").status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT import_batch_id FROM customers WHERE id IN (?, ?)",
            (bad["id"], plain["id"]),
        ).fetchall() == [(None,), (None,)]


def test_expired_batch_returns_410_without_deleting(client, db_path):
    c = _customer(client, "오래된 배치", BATCH_A)
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE customers SET created_at=? WHERE id=?", (old, c["id"]))
        conn.commit()
    r = client.post(f"/capture/batches/{BATCH_A}/undo")
    assert r.status_code == 410
    assert r.json() == {"detail": "되돌릴 수 있는 시간이 지났습니다."}
    assert client.get(f"/customers/{c['id']}").status_code == 200


def test_list_batches_excludes_null_and_has_exact_expiry(client):
    _customer(client, "배치", BATCH_A)
    _customer(client, "NULL")
    r = client.get("/capture/batches", params={"limit": 5})
    assert r.status_code == 200
    batches = r.json()["batches"]
    assert len(batches) == 1
    item = batches[0]
    assert item["id"] == BATCH_A
    assert item["counts"] == {"customers": 1, "policies": 0, "consultations": 0}
    assert item["undoable"] is True
    assert datetime.fromisoformat(item["expires_at"]) == (
        datetime.fromisoformat(item["created_at"]) + timedelta(minutes=30)
    )


def test_legacy_init_schema_adds_batch_columns_and_table_idempotently(tmp_path):
    from database.db import SCHEMA, init_schema

    legacy_schema = SCHEMA.replace("    import_batch_id TEXT,\n", "").replace(
        "CREATE TABLE IF NOT EXISTS import_batch_rrn (\n"
        "    batch_id    TEXT NOT NULL,\n"
        "    customer_id TEXT NOT NULL,\n"
        "    PRIMARY KEY (batch_id, customer_id)\n"
        ");\n\n",
        "",
    )
    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO customers (id, name, created_at, updated_at) VALUES ('old', 'name', 'now', 'now')"
    )
    conn.commit()

    init_schema(conn)
    init_schema(conn)
    for table in ("customers", "policies", "consultations"):
        assert "import_batch_id" in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    assert conn.execute("SELECT import_batch_id FROM customers WHERE id='old'").fetchone()[0] is None
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='import_batch_rrn'"
    ).fetchone()[0] == "import_batch_rrn"
    conn.close()
