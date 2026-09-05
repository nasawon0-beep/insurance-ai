"""
평문(암호화 도입 전) PII 자동 재암호화 (read-repair, P2-B A안).

- enc:v1 표식 없는 값을 raw INSERT → API 로 읽으면 평문 그대로 나오고,
  그 뒤 DB row 는 enc:v1: 로 바뀌어 있어야 한다.
- 재-GET 시 추가 재암호화(다른 ciphertext)가 일어나지 않아야 한다 (멱등).
- count_plaintext_values / /health 의 plaintext_count·warning 노출.

임시 DB(CUSTOMER_DB_PATH)와 고정 키(CUSTOMER_DB_KEY_B64)만 쓴다.
"""
import base64
import sqlite3

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()
    yield tmp_path / "customers.sqlite3"
    crypto.reset_cache()


@pytest.fixture
def client(db_path):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def _raw(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _init(db_path):
    from database.db import connect, init_schema

    conn = connect()
    try:
        init_schema(conn)
    finally:
        conn.close()


def _insert_plaintext_customer(db_path, cid, name="평문고객", phone="010-1234-5678"):
    conn = _raw(db_path)
    conn.execute(
        "INSERT INTO customers (id, name, phone, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (cid, name, phone, "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_plaintext_field_reencrypted_on_read(client, db_path):
    _init(db_path)
    _insert_plaintext_customer(db_path, "c-plain-1")

    # raw 로는 평문
    conn = _raw(db_path)
    row = conn.execute("SELECT name, phone FROM customers WHERE id = ?", ("c-plain-1",)).fetchone()
    conn.close()
    assert row["name"] == "평문고객" and row["phone"] == "010-1234-5678"

    # API 로 읽으면 복호화 경로를 타고 평문이 그대로 나온다
    got = client.get("/customers/c-plain-1")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["name"] == "평문고객" and body["phone"] == "010-1234-5678"

    # 이제 DB row 는 재암호화되어 있어야 한다
    conn = _raw(db_path)
    row = conn.execute("SELECT name, phone FROM customers WHERE id = ?", ("c-plain-1",)).fetchone()
    conn.close()
    assert row["name"].startswith("enc:v1:")
    assert row["phone"].startswith("enc:v1:")


def test_reencryption_is_idempotent(client, db_path):
    _init(db_path)
    _insert_plaintext_customer(db_path, "c-plain-2")

    assert client.get("/customers/c-plain-2").status_code == 200

    conn = _raw(db_path)
    after_first = conn.execute(
        "SELECT name, phone FROM customers WHERE id = ?", ("c-plain-2",)
    ).fetchone()
    conn.close()
    assert after_first["name"].startswith("enc:v1:")

    # 두 번째 읽기 — 이미 암호화라 repairs 가 비고, ciphertext 도 그대로여야 한다
    assert client.get("/customers/c-plain-2").status_code == 200

    conn = _raw(db_path)
    after_second = conn.execute(
        "SELECT name, phone FROM customers WHERE id = ?", ("c-plain-2",)
    ).fetchone()
    conn.close()
    assert after_second["name"] == after_first["name"]
    assert after_second["phone"] == after_first["phone"]


def test_count_plaintext_values(client, db_path):
    from database.db import connect
    from database.repo import count_plaintext_values

    _init(db_path)
    _insert_plaintext_customer(db_path, "c-count-1", name="갑", phone="010-0000-0001")
    _insert_plaintext_customer(db_path, "c-count-2", name="을", phone="010-0000-0002")

    conn = connect()
    try:
        # 고객 2명 × 평문 필드 2개(name, phone) = 4
        assert count_plaintext_values(conn) == 4
    finally:
        conn.close()

    # 전부 읽어서 재암호화
    assert client.get("/customers").status_code == 200

    conn = connect()
    try:
        assert count_plaintext_values(conn) == 0
    finally:
        conn.close()


def test_health_reports_plaintext_count_and_warning(client, db_path):
    _init(db_path)

    # 평문 없음 → plaintext_count 0, warning 없음
    body = client.get("/health").json()["customer_db"]
    assert body["plaintext_count"] == 0
    assert "warning" not in body

    # 평문 심으면 count > 0 + warning True
    _insert_plaintext_customer(db_path, "c-health-1")
    body = client.get("/health").json()["customer_db"]
    assert body["plaintext_count"] > 0
    assert body["warning"] is True


def test_encrypted_crud_still_roundtrips(client):
    """13개 call site 시그니처 변경 회귀 — 정상 CRUD 는 그대로."""
    c = client.post("/customers", json={"name": "정상고객", "phone": "010-9999-0000"}).json()
    cid = c["id"]
    p = client.post(f"/customers/{cid}/policies", json={"insurer": "A생명"}).json()
    k = client.post(
        f"/customers/{cid}/consultations", json={"title": "상담1", "content": "내용"}
    ).json()

    got = client.get(f"/customers/{cid}").json()
    assert got["name"] == "정상고객"
    assert got["policies"][0]["insurer"] == "A생명"
    assert got["consultations"][0]["title"] == "상담1"
    assert client.patch(f"/policies/{p['id']}", json={"memo": "메모"}).status_code == 200
    assert client.patch(f"/consultations/{k['id']}", json={"content": "수정"}).status_code == 200


# ---------- 정책 / 상담 / 파생 경로 repair (codex-critic 추가 요구) ----------

def _enc(value):
    from database.crypto import get_cipher

    return get_cipher().encrypt(value)


def test_policy_plaintext_reencrypted_via_customer_detail(client, db_path):
    """정책 암호화 필드(insurer, end_date)를 평문으로 심고 고객 상세를 열면 재암호화된다."""
    _init(db_path)
    c = client.post("/customers", json={"name": "정책고객"}).json()
    cid = c["id"]

    conn = _raw(db_path)
    conn.execute(
        "INSERT INTO policies (id, customer_id, insurer, end_date, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
        ("p-plain-1", cid, "숨김생명", "2030-01-01", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    got = client.get(f"/customers/{cid}").json()
    pol = got["policies"][0]
    assert pol["insurer"] == "숨김생명" and pol["end_date"] == "2030-01-01"

    conn = _raw(db_path)
    row = conn.execute("SELECT insurer, end_date FROM policies WHERE id = 'p-plain-1'").fetchone()
    conn.close()
    assert row["insurer"].startswith("enc:v1:")
    assert row["end_date"].startswith("enc:v1:")


def test_enrich_customer_repairs_plaintext_policy_end_date(client, db_path):
    """고객 목록(_enrich_customer)의 단발 end_date 복호화 경로도 평문을 재암호화한다."""
    _init(db_path)
    c = client.post("/customers", json={"name": "목록고객"}).json()
    cid = c["id"]

    conn = _raw(db_path)
    conn.execute(
        "INSERT INTO policies (id, customer_id, insurer, end_date, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
        ("p-plain-2", cid, _enc("암호화생명"), "2031-05-05", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    # 고객 목록만 호출 (정책 목록/상세는 안 엶)
    assert client.get("/customers").status_code == 200

    conn = _raw(db_path)
    row = conn.execute("SELECT end_date FROM policies WHERE id = 'p-plain-2'").fetchone()
    conn.close()
    assert row["end_date"].startswith("enc:v1:")


def test_all_tags_repairs_plaintext_tags(client, db_path):
    """/tags 만 호출하는 흐름에서도 customers.tags 평문이 재암호화된다."""
    _init(db_path)
    conn = _raw(db_path)
    conn.execute(
        "INSERT INTO customers (id, name, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("c-tags-1", _enc("태그고객"), "VIP,장기", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    body = client.get("/tags").json()["tags"]
    assert "VIP" in body and "장기" in body

    conn = _raw(db_path)
    row = conn.execute("SELECT tags FROM customers WHERE id = 'c-tags-1'").fetchone()
    conn.close()
    assert row["tags"].startswith("enc:v1:")


def test_consultation_transcript_reencrypted_on_read(client, db_path):
    """상담의 추가 암호화 필드(transcript, coverage_json)도 재암호화된다."""
    _init(db_path)
    c = client.post("/customers", json={"name": "상담고객"}).json()
    cid = c["id"]

    conn = _raw(db_path)
    conn.execute(
        "INSERT INTO consultations (id, customer_id, consulted_at, title, transcript, coverage_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "k-plain-1", cid, "2024-01-01", _enc("제목"),
            "녹취 원문 평문", '{"암":1000}',
            "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    got = client.get(f"/customers/{cid}/consultations").json()["consultations"]
    assert got[0]["transcript"] == "녹취 원문 평문"

    conn = _raw(db_path)
    row = conn.execute(
        "SELECT transcript, coverage_json FROM consultations WHERE id = 'k-plain-1'"
    ).fetchone()
    conn.close()
    assert row["transcript"].startswith("enc:v1:")
    assert row["coverage_json"].startswith("enc:v1:")


def test_read_repair_does_not_clobber_concurrent_write(client, db_path):
    """CAS: SELECT 이후 다른 연결이 같은 필드를 바꾸면 repair 는 그 값을 덮지 않는다."""
    from database.db import connect
    from database import repo

    _init(db_path)
    _insert_plaintext_customer(db_path, "c-cas-1", name="원본", phone="010-1111-1111")

    conn = connect()
    stale_row = conn.execute("SELECT * FROM customers WHERE id = 'c-cas-1'").fetchone()

    # 다른 연결이 그 사이 name 을 PATCH
    other = _raw(db_path)
    other.execute(
        "UPDATE customers SET name = ? WHERE id = 'c-cas-1'", (_enc("새이름"),)
    )
    other.commit()
    other.close()

    # 이제 오래된 row 로 repair 시도 — name CAS 는 실패해야(rowcount 0) 한다
    repo._decrypt_row(conn, "customers", stale_row, repo._CUSTOMER_ENC)
    conn.close()

    conn = _raw(db_path)
    row = conn.execute("SELECT name, phone FROM customers WHERE id = 'c-cas-1'").fetchone()
    conn.close()
    from database.crypto import get_cipher

    assert get_cipher().decrypt(row["name"]) == "새이름"  # PATCH 값 보존
    assert row["phone"].startswith("enc:v1:")  # 경쟁 없던 필드는 정상 repair


def test_health_degrades_on_db_error(client, db_path, monkeypatch):
    """count_plaintext_values 가 터져도 /health 는 200 + customer_db.error 로 degrade."""
    _init(db_path)
    import database.repo as repo

    def boom(conn):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(repo, "count_plaintext_values", boom)
    body = client.get("/health").json()
    assert body["customer_db"]["encryption"] == "unavailable"
    assert "error" in body["customer_db"]
