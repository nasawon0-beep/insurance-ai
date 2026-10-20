"""데이터 내보내기 테스트 (database/export_data.py + GET /export)."""
import base64
import csv
import io
import sqlite3
import zipfile

import pytest

pytest.importorskip("httpx")

_KEY = base64.b64encode(b"0" * 32).decode("ascii")
_SYNTHETIC_RRN_A = "900101" + "1234567"
_SYNTHETIC_RRN_B = "900102" + "2234567"


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


def _open_zip(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def _rows(raw: bytes):
    text = raw.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_zip_has_three_csvs_and_readme(client):
    c = client.post("/customers", json={"name": "김포장", "phone": "010-1234-5678"}).json()
    client.post(f"/customers/{c['id']}/policies", json={"insurer": "ACME생명", "product_name": "건강보험"})
    client.post(f"/customers/{c['id']}/consultations", json={"title": "가입 상담", "content": "암보험 관심"})

    r = client.get("/export")
    assert r.status_code == 200, r.text
    files = _open_zip(r.json()["path"])
    assert set(files) == {"고객.csv", "계약.csv", "상담.csv", "README.txt"}

    # BOM
    assert files["고객.csv"][:3] == b"\xef\xbb\xbf"
    assert files["README.txt"][:3] == b"\xef\xbb\xbf"

    cust = _rows(files["고객.csv"])
    assert cust[0][:2] == ["id", "name"]
    assert any("김포장" in row for row in cust[1:])           # 복호화된 값
    pol = _rows(files["계약.csv"])
    assert any("ACME생명" in row and "김포장" in row for row in pol[1:])  # 고객명 조인
    con = _rows(files["상담.csv"])
    assert "transcript" not in con[0] and "coverage_json" not in con[0]
    assert any("가입 상담" in row for row in con[1:])

    assert "주민등록번호 포함 여부: 제외" in files["README.txt"].decode("utf-8-sig")


def test_rrn_column_is_omitted_from_default_export(client):
    client.post("/customers", json={"name": "무주민"})
    files = _open_zip(client.get("/export").json()["path"])
    assert "rrn" not in _rows(files["고객.csv"])[0]


def test_legacy_env_on_does_not_expose_rrn_in_export(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    client.post("/customers", json={"name": "합성주민A", "rrn": _SYNTHETIC_RRN_A})
    response = client.get("/export")
    assert response.status_code == 200 and response.json()["include_rrn"] is False
    files = _open_zip(response.json()["path"])
    assert "rrn" not in _rows(files["고객.csv"])[0]


def test_explicit_internal_rrn_export_requires_and_runs_audit_callback(client, env):
    from database import export_data, repo
    from database.db import connect, init_schema

    first = client.post("/customers", json={"name": "합성주민A", "rrn": _SYNTHETIC_RRN_A}).json()
    second = client.post("/customers", json={"name": "합성주민B", "rrn": _SYNTHETIC_RRN_B}).json()
    client.post("/customers", json={"name": "주민없음"})

    conn = connect(env / "data" / "customers.sqlite3")
    init_schema(conn)
    try:
        result = export_data.build_export(
            conn,
            include_rrn=True,
            rrn_audit=lambda customer_ids: repo.log_rrn_access_many(
                conn, customer_ids, "승인된 내부 내보내기", "exporter-1", "export"
            ),
        )
    finally:
        conn.close()
    assert result["include_rrn"] is True
    with sqlite3.connect(env / "data" / "customers.sqlite3") as conn:
        rows = conn.execute(
            "SELECT customer_id, purpose, actor FROM rrn_access_log "
            "WHERE access_type = 'export' ORDER BY customer_id"
        ).fetchall()
    assert {row[0] for row in rows} == {first["id"], second["id"]}
    assert all(row[1] == "승인된 내부 내보내기" for row in rows)
    assert all(row[2] == "exporter-1" for row in rows)


def test_default_export_never_logs_or_exposes_rrn(client, env, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    customer = client.post(
        "/customers", json={"name": "합성항상저장", "rrn": _SYNTHETIC_RRN_A}
    ).json()
    exported = client.get("/export", headers={"X-Actor-Id": "exporter-off-env"})
    assert exported.status_code == 200 and exported.json()["include_rrn"] is False

    files = _open_zip(exported.json()["path"])
    assert "rrn" not in _rows(files["고객.csv"])[0]
    with sqlite3.connect(env / "data" / "customers.sqlite3") as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM rrn_access_log "
            "WHERE customer_id = ? AND access_type = 'export'", (customer["id"],)
        ).fetchone()[0]
    assert count == 0


def test_explicit_rrn_export_fails_before_file_when_audit_fails(client, env, monkeypatch):
    from database import export_data, repo
    from database.db import connect, init_schema

    client.post("/customers", json={"name": "합성감사실패", "rrn": _SYNTHETIC_RRN_A})
    exports = env / "data" / "exports"
    before = set(exports.glob("export-*.zip")) if exports.exists() else set()

    def fail_audit(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(repo, "log_rrn_access_many", fail_audit)
    conn = connect(env / "data" / "customers.sqlite3")
    init_schema(conn)
    try:
        with pytest.raises(sqlite3.OperationalError, match="audit unavailable"):
            export_data.build_export(
                conn,
                include_rrn=True,
                rrn_audit=lambda customer_ids: repo.log_rrn_access_many(
                    conn, customer_ids, "승인된 내부 내보내기", "tester", "export"
                ),
            )
    finally:
        conn.close()
    after = set(exports.glob("export-*.zip")) if exports.exists() else set()
    assert after == before


def test_csv_injection_guarded(client):
    client.post("/customers", json={"name": "=SUM(A1:A9)", "memo": "+CMD"})
    files = _open_zip(client.get("/export").json()["path"])
    cust = _rows(files["고객.csv"])
    joined = ["|".join(r) for r in cust[1:]]
    assert any("'=SUM(A1:A9)" in j for j in joined)
    assert any("'+CMD" in j for j in joined)


def test_list_exports(client):
    client.post("/customers", json={"name": "x"})
    client.get("/export")
    client.get("/export")
    lst = client.get("/exports").json()["exports"]
    assert len(lst) == 2 and all(e["filename"].endswith(".zip") for e in lst)
