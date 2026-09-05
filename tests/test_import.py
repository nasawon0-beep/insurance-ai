"""고객 CSV 가져오기 테스트 (database/import_data.py + /import/*)."""
import base64
import io
import json
from datetime import datetime

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


CSV = (
    "이름,전화,생년월일,태그\n"
    "홍길동,01011112222,1990.01.02,VIP;암보험\n"
    "김철수,010-3333-4444,19851231,\n"
    ",010-5555-6666,2000-01-01,무명\n"  # 이름 없음 → 실패 행
)


def _commit(client, csv_text, mapping, dedupe="merge", filename="c.csv", encoding="utf-8"):
    return client.post(
        "/import/commit",
        files={"file": (filename, csv_text.encode(encoding), "text/csv")},
        data={"mapping": json.dumps(mapping), "dedupe": dedupe},
    )


def test_preview_columns_and_encoding(client):
    r = client.post("/import/preview", files={"file": ("c.csv", CSV.encode("utf-8"), "text/csv")})
    assert r.status_code == 200
    b = r.json()
    assert b["columns"] == ["이름", "전화", "생년월일", "태그"]
    assert b["encoding"] in ("utf-8", "utf-8-sig")
    assert b["row_count"] == 3
    assert len(b["sample_rows"]) == 3


def test_preview_detects_cp949(client):
    raw = "이름,전화\n가나다,010-1111-2222\n".encode("cp949")
    r = client.post("/import/preview", files={"file": ("c.csv", raw, "text/csv")})
    assert r.json()["encoding"] == "cp949"


def test_commit_maps_and_normalizes(client):
    r = _commit(client, CSV, {"name": "이름", "phone": "전화", "birth_date": "생년월일", "tags": "태그"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["created"] == 2
    assert len(b["failed"]) == 1 and b["failed"][0]["row"] == 4

    cs = client.get("/customers").json()["customers"]
    hong = next(c for c in cs if c["name"] == "홍길동")
    assert hong["phone"] == "010-1111-2222"          # 11자리 재하이픈
    assert hong["birth_date"] == "1990-01-02"         # YYYY.MM.DD 정규화
    assert set(hong["tags"]) == {"VIP", "암보험"}       # ;,  분리
    kim = next(c for c in cs if c["name"] == "김철수")
    assert kim["birth_date"] == "1985-12-31"          # YYYYMMDD


def test_name_unmapped_422(client):
    r = _commit(client, CSV, {"phone": "전화"})
    assert r.status_code == 422


def test_dedupe_merge_new_skip(client):
    client.post("/customers", json={"name": "박영수", "phone": "010-7777-8888"})
    one = "이름,전화,주소\n박영수,010-7777-8888,서울시 강남구\n"

    # merge: 빈 칸(주소)만 채운다
    r = _commit(client, one, {"name": "이름", "phone": "전화", "address": "주소"}, "merge")
    assert r.json()["merged"] == 1 and r.json()["created"] == 0
    got = client.get("/customers").json()["customers"][0]
    assert got["address"] == "서울시 강남구"

    # skip: 아무 것도 안 함
    r = _commit(client, one, {"name": "이름", "phone": "전화", "address": "주소"}, "skip")
    assert r.json()["skipped"] == 1
    assert len(client.get("/customers").json()["customers"]) == 1

    # new: 항상 신규
    r = _commit(client, one, {"name": "이름", "phone": "전화", "address": "주소"}, "new")
    assert r.json()["created"] == 1
    assert len(client.get("/customers").json()["customers"]) == 2


def test_name_only_match_is_added_as_new(client):
    """이름만 일치(전화·생년월일 없음) → 병합하지 않고 신규 + 경고."""
    client.post("/customers", json={"name": "동명이인"})
    r = _commit(client, "이름,주소\n동명이인,대전\n", {"name": "이름", "address": "주소"}, "merge")
    b = r.json()
    assert b["created"] == 1 and b["merged"] == 0
    assert any("이름만 일치" in w for w in b["warnings"])
    assert len(client.get("/customers").json()["customers"]) == 2

    # skip 모드여도 이름만 일치는 건너뛰지 않는다
    client.post("/customers", json={"name": "동명이인2"})
    r = _commit(client, "이름\n동명이인2\n", {"name": "이름"}, "skip")
    assert r.json()["created"] == 1 and r.json()["skipped"] == 0


def test_rrn_column_ignored_when_flag_off(client):
    csv_text = "이름,주민번호\n최주민,900101-1234567\n"
    r = _commit(client, csv_text, {"name": "이름", "rrn": "주민번호"})
    b = r.json()
    assert b["created"] == 1
    assert b["ignored_rrn"] is True
    assert any("주민번호 컬럼 무시" in w for w in b["warnings"])
    c = client.get("/customers").json()["customers"][0]
    assert c["has_rrn"] is False


def test_rrn_imported_when_flag_on(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    csv_text = "이름,주민번호\n최주민,900101-1234567\n"
    r = _commit(client, csv_text, {"name": "이름", "rrn": "주민번호"})
    assert r.json()["created"] == 1
    c = client.get("/customers").json()["customers"][0]
    assert c["has_rrn"] is True


def _xlsx_bytes(rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def test_xlsx_preview_and_commit(client):
    raw = _xlsx_bytes([
        [None, None, None],
        ["이름", "전화", "생년월일"],
        ["엑셀고객", 1011112222, "1990.01.02"],
        [None, None, None],
    ])
    files = {"file": ("book.xlsx", raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post("/import/preview", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["이름", "전화", "생년월일"]
    assert r.json()["encoding"] == "xlsx"
    assert r.json()["row_count"] == 1
    assert r.json()["sample_rows"] == [{"이름": "엑셀고객", "전화": "1011112222", "생년월일": "1990.01.02"}]

    r = client.post(
        "/import/commit",
        files=files,
        data={"mapping": json.dumps({"name": "이름", "phone": "전화", "birth_date": "생년월일"})},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    customer = client.get("/customers").json()["customers"][0]
    assert customer["name"] == "엑셀고객"
    assert customer["phone"] == "010-1111-2222"
    assert customer["birth_date"] == "1990-01-02"


def test_xlsx_date_cell_is_normalized(client):
    raw = _xlsx_bytes([["이름", "생년월일"], ["날짜고객", datetime(1992, 3, 4, 15, 30)]])
    r = client.post(
        "/import/commit",
        files={"file": ("book.xlsx", raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mapping": json.dumps({"name": "이름", "birth_date": "생년월일"})},
    )
    assert r.status_code == 200, r.text
    assert client.get("/customers").json()["customers"][0]["birth_date"] == "1992-03-04"


def test_xlsx_numeric_rrn_with_missing_leading_zero_fails_row(client, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    raw = _xlsx_bytes([["이름", "주민번호"], ["주민고객", 101123456789]])
    r = client.post(
        "/import/commit",
        files={"file": ("book.xlsx", raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mapping": json.dumps({"name": "이름", "rrn": "주민번호"})},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert r.json()["failed"][0]["row"] == 2
    assert "앞자리 0" in r.json()["failed"][0]["reason"]


def test_xlsx_other_numeric_phone_is_not_silently_saved(client):
    raw = _xlsx_bytes([["이름", "전화"], ["전화고객", 2123456789]])
    r = client.post(
        "/import/commit",
        files={"file": ("book.xlsx", raw, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mapping": json.dumps({"name": "이름", "phone": "전화"})},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert "텍스트로 다시 저장" in r.json()["failed"][0]["reason"]


@pytest.mark.parametrize("headers", [["이름", "전화", "전화"], ["이름", None]])
def test_xlsx_invalid_headers_return_400(client, headers):
    raw = _xlsx_bytes([headers, ["고객", "01011112222", "01022223333"][:len(headers)]])
    r = client.post("/import/preview", files={"file": ("book.xlsx", raw, "application/octet-stream")})
    assert r.status_code == 400
    assert "헤더(첫 행)" in r.json()["detail"]


def test_corrupt_xlsx_returns_4xx_for_preview_and_commit(client):
    files = {"file": ("book.xlsx", b"not an xlsx", "application/octet-stream")}
    preview = client.post("/import/preview", files=files)
    commit = client.post(
        "/import/commit", files=files, data={"mapping": json.dumps({"name": "이름"})}
    )
    assert 400 <= preview.status_code < 500
    assert 400 <= commit.status_code < 500
    assert "엑셀 파일을 읽을 수 없습니다" in preview.json()["detail"]


def test_empty_xlsx_sheet_returns_clear_400(client):
    raw = _xlsx_bytes([])
    r = client.post("/import/preview", files={"file": ("book.xlsx", raw, "application/octet-stream")})
    assert r.status_code == 400
    assert "헤더(첫 행)" in r.json()["detail"]


def test_xls_rejected(client):
    r = client.post("/import/preview", files={"file": ("book.xls", b"old excel", "application/vnd.ms-excel")})
    assert r.status_code == 415
    assert "지원하지 않는 형식" in r.json()["detail"]


def test_yymmdd_pivot_not_hardcoded():
    from database import import_data

    assert import_data._norm_birth("991231")[0] == "1999-12-31"   # 99 > 올해 두자리 → 1900대
    assert import_data._norm_birth("050301")[0] == "2005-03-01"   # 05 <= 올해 두자리 → 2000대
    # 8자리/구분자 경로는 피벗과 무관
    assert import_data._norm_birth("2001.02.03")[0] == "2001-02-03"


def test_bad_birthdate_is_warning_not_rejection(client):
    csv_text = "이름,생년월일\n정메모,없음\n"
    r = _commit(client, csv_text, {"name": "이름", "birth_date": "생년월일"})
    b = r.json()
    assert b["created"] == 1 and b["failed"] == []
    assert any("생년월일" in w for w in b["warnings"])
    c = client.get("/customers").json()["customers"][0]
    assert c["birth_date"] in (None, "")
