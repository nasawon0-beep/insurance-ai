"""데이터 내보내기 테스트 (database/export_data.py + GET /export)."""
import base64
import csv
import io
import sqlite3
import zipfile

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


def test_rrn_omitted_when_flag_off(client):
    client.post("/customers", json={"name": "무주민"})
    files = _open_zip(client.get("/export").json()["path"])
    assert "rrn" not in _rows(files["고객.csv"])[0]


def test_rrn_included_when_flag_on(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    client.post("/customers", json={"name": "주민있음", "rrn": "900101-1234567"})
    files = _open_zip(client.get("/export").json()["path"])
    header = _rows(files["고객.csv"])[0]
    assert "rrn" in header
    idx = header.index("rrn")
    body = _rows(files["고객.csv"])[1:]
    assert any(row[idx] == "9001011234567" for row in body)


def test_rrn_export_logs_each_included_customer(client, env, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    first = client.post("/customers", json={"name": "주민1", "rrn": "9001011234567"}).json()
    second = client.post("/customers", json={"name": "주민2", "rrn": "9001022234567"}).json()
    client.post("/customers", json={"name": "주민없음"})

    r = client.get("/export", headers={"X-Actor-Id": "exporter-1"})
    assert r.status_code == 200 and r.json()["include_rrn"] is True
    with sqlite3.connect(env / "data" / "customers.sqlite3") as conn:
        rows = conn.execute(
            "SELECT customer_id, purpose, actor FROM rrn_access_log "
            "WHERE access_type = 'export' ORDER BY customer_id"
        ).fetchall()
    assert {row[0] for row in rows} == {first["id"], second["id"]}
    assert all(row[1] == "데이터 내보내기(고객.csv)" for row in rows)
    assert all(row[2] == "exporter-1" for row in rows)


def test_rrn_export_does_not_log_when_flag_off_or_no_rrn(client, env, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    client.post("/customers", json={"name": "토글꺼짐", "rrn": "9001011234567"})
    assert client.get("/export").status_code == 200
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    assert client.get("/export").status_code == 200

    with sqlite3.connect(env / "data" / "customers.sqlite3") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM rrn_access_log WHERE access_type = 'export'"
        ).fetchone()[0] == 0


def test_rrn_export_fails_before_file_when_audit_fails(client, env, monkeypatch):
    from database import repo

    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    client.post("/customers", json={"name": "감사실패", "rrn": "9001011234567"})
    exports = env / "data" / "exports"
    before = set(exports.glob("export-*.zip")) if exports.exists() else set()

    def fail_audit(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(repo, "log_rrn_access_many", fail_audit)
    with pytest.raises(sqlite3.OperationalError, match="audit unavailable"):
        client.get("/export")
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
