"""
고객 관리 계약 테스트: 고객 CRUD + 고객별 보험계약 CRUD + CASCADE 삭제 + 저장 시 암호화.

임시 DB(CUSTOMER_DB_PATH)와 고정 키(CUSTOMER_DB_KEY_B64)를 주입한다.
실제 파일도 OS 키체인도 건드리지 않는다.
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


@pytest.fixture
def rrn_on(monkeypatch):
    """RRN 입력 파일럿 토글을 ON 으로 강제 (env 잠금). 주민번호 전체 동작 커버용."""
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    yield


def _make_customer(client, name="홍길동", phone="010-1111-2222"):
    r = client.post("/customers", json={"name": name, "phone": phone})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_get_customer(client):
    c = _make_customer(client)
    assert c["id"] and c["name"] == "홍길동"
    assert c["created_at"] and c["updated_at"]

    got = client.get(f"/customers/{c['id']}")
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == c["id"]
    assert body["policies"] == []  # 아직 계약 없음


@pytest.mark.parametrize("query", ["1", "a1", "12"])
def test_short_or_mixed_numeric_query_does_not_match_phone(client, query):
    _make_customer(client, name="전화검색대상", phone="010-1111-2222")
    assert client.get("/customers", params={"q": query}).json()["customers"] == []


def test_three_digit_phone_query_matches_normalized_phone(client):
    customer = _make_customer(client, name="전화검색대상", phone="010-1111-2222")
    found = client.get("/customers", params={"q": "010"}).json()["customers"]
    assert [row["id"] for row in found] == [customer["id"]]


@pytest.mark.parametrize("birth_date", ["2026-02-31", "1990-04-31"])
def test_customer_rejects_invalid_calendar_birth_date(client, birth_date):
    assert client.post(
        "/customers", json={"name": "달력 오류", "birth_date": birth_date}
    ).status_code == 422


@pytest.mark.parametrize("payload", [{}, {"birth_date": ""}, {"birth_date": "1990-12-25"}])
def test_customer_accepts_valid_or_empty_birth_date(client, payload):
    assert client.post("/customers", json={"name": "달력 정상", **payload}).status_code == 201


def test_customer_patch_rejects_invalid_calendar_birth_date(client):
    customer = _make_customer(client)
    assert client.patch(
        f"/customers/{customer['id']}", json={"birth_date": "2026-02-31"}
    ).status_code == 422


@pytest.mark.parametrize(("gender", "normalized"), [("남", "M"), ("여자", "F")])
def test_customer_normalizes_gender(client, gender, normalized):
    created = client.post("/customers", json={"name": "성별", "gender": gender})
    assert created.status_code == 201
    assert client.get(f"/customers/{created.json()['id']}").json()["gender"] == normalized


def test_customer_rejects_unknown_gender(client):
    assert client.post(
        "/customers", json={"name": "성별 오류", "gender": "XYZ"}
    ).status_code == 422


@pytest.mark.parametrize(("email", "expected"), [("a@b.com", 201), ("", 201), ("not-an-email", 422)])
def test_customer_validates_email(client, email, expected):
    assert client.post(
        "/customers", json={"name": "이메일", "email": email}
    ).status_code == expected


def test_list_and_search(client):
    _make_customer(client, name="김철수", phone="010-3333-4444")
    _make_customer(client, name="이영희", phone="010-5555-6666")

    allc = client.get("/customers").json()["customers"]
    assert {c["name"] for c in allc} == {"김철수", "이영희"}

    hit = client.get("/customers", params={"q": "영희"}).json()["customers"]
    assert len(hit) == 1 and hit[0]["name"] == "이영희"

    by_phone = client.get("/customers", params={"q": "3333"}).json()["customers"]
    assert len(by_phone) == 1 and by_phone[0]["name"] == "김철수"


def test_searches_extended_customer_policy_and_normalized_phone_fields(client):
    target = client.post("/customers", json={
        "name": "검색대상",
        "phone": "010-1234-5678",
        "email": "target@example.com",
        "address": "서울시 마포구",
        "memo": "갱신 상담 필요",
        "occupation": "도예가",
        "tags": ["우수고객"],
    }).json()
    _make_customer(client, name="다른고객", phone="010-9999-8888")
    policy = client.post(f"/customers/{target['id']}/policies", json={
        "insurer": "한빛손해보험",
        "product_name": "든든건강플랜",
        "policy_number": "POL-SEARCH-77",
    })
    assert policy.status_code == 201, policy.text

    for query in [
        "target@example.com", "마포구", "갱신 상담", "도예가", "우수고객",
        "한빛손해보험", "든든건강플랜", "pol-search-77", "01012345678", "1234",
    ]:
        found = client.get("/customers", params={"q": query}).json()["customers"]
        assert [c["id"] for c in found] == [target["id"]], query

    assert client.get("/customers", params={"q": "어디에도없는검색어"}).json()["customers"] == []
    assert len(client.get("/customers").json()["customers"]) == 2


def test_patch_customer_partial(client):
    c = _make_customer(client)
    r = client.patch(f"/customers/{c['id']}", json={"memo": "VIP 고객", "address": "서울"})
    assert r.status_code == 200
    b = r.json()
    assert b["memo"] == "VIP 고객" and b["address"] == "서울"
    assert b["name"] == "홍길동"  # 안 보낸 필드는 유지
    assert b["updated_at"] >= c["updated_at"]


def test_patch_customer_rejects_duplicate_rrn(client, rrn_on):
    first = client.post(
        "/customers", json={"name": "첫 고객", "rrn": "900101-1234567"}
    )
    second = client.post(
        "/customers", json={"name": "둘째 고객", "rrn": "900102-2234567"}
    )
    assert first.status_code == second.status_code == 201

    r = client.patch(
        f"/customers/{second.json()['id']}", json={"rrn": "900101-1234567"}
    )
    assert r.status_code == 409
    assert "같은 주민등록번호" in r.json()["detail"]


def test_patch_customer_accepts_same_rrn_and_preserves_it_when_omitted(client, rrn_on):
    created = client.post(
        "/customers", json={"name": "첫 고객", "rrn": "900101-1234567"}
    )
    assert created.status_code == 201
    cid = created.json()["id"]

    same = client.patch(f"/customers/{cid}", json={"rrn": "900101-1234567"})
    assert same.status_code == 200
    omitted = client.patch(f"/customers/{cid}", json={"memo": "유지 확인"})
    assert omitted.status_code == 200
    assert client.get(f"/customers/{cid}").json()["rrn"] == "9001011234567"


def test_unique_constraint_race_is_mapped_to_409(client, rrn_on, monkeypatch):
    from database import repo

    first = client.post(
        "/customers", json={"name": "첫 고객", "rrn": "900101-1234567"}
    )
    assert first.status_code == 201
    monkeypatch.setattr(repo, "find_customer_id_by_rrn", lambda *args, **kwargs: None)

    raced = client.post(
        "/customers", json={"name": "둘째 고객", "rrn": "900101-1234567"}
    )
    assert raced.status_code == 409
    assert raced.json()["detail"] == "같은 주민등록번호의 고객이 이미 있습니다"


def test_patch_unique_constraint_race_is_mapped_to_409(client, rrn_on, monkeypatch):
    from database import repo

    first = client.post(
        "/customers", json={"name": "첫 고객", "rrn": "900101-1234567"}
    ).json()
    second = client.post(
        "/customers", json={"name": "둘째 고객", "rrn": "900102-2234567"}
    ).json()
    monkeypatch.setattr(repo, "find_customer_id_by_rrn", lambda *args, **kwargs: None)

    raced = client.patch(
        f"/customers/{second['id']}", json={"rrn": "900101-1234567"}
    )
    assert raced.status_code == 409
    assert raced.json()["detail"] == "같은 주민등록번호의 고객이 이미 있습니다"
    assert client.get(f"/customers/{first['id']}").status_code == 200


def test_rrn_hash_index_is_unique(client, db_path):
    client.get("/customers")
    with sqlite3.connect(db_path) as conn:
        indexes = {row[1]: row for row in conn.execute("PRAGMA index_list(customers)")}
    assert indexes["idx_customers_rrn_hash"][2] == 1


def test_repeated_requests_do_not_recreate_rrn_index(client, db_path):
    client.get("/customers")
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("PRAGMA schema_version").fetchone()[0]
    client.get("/customers")
    client.get("/customers")
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("PRAGMA schema_version").fetchone()[0]
    assert after == before


def test_legacy_duplicate_rrn_migration_fails(tmp_path):
    from database.db import _migrate_rrn_unique

    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE customers (id TEXT PRIMARY KEY, rrn_hash TEXT);
        CREATE INDEX idx_customers_rrn_hash ON customers(rrn_hash);
        INSERT INTO customers VALUES ('a', 'duplicate');
        INSERT INTO customers VALUES ('b', 'duplicate');
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="중복 rrn_hash 1개"):
        _migrate_rrn_unique(conn)
    index = conn.execute("PRAGMA index_list(customers)").fetchone()
    assert index["unique"] == 0
    conn.close()


def test_first_registered_ym_defaults_and_is_editable(client):
    import datetime as _dt

    c = _make_customer(client)
    # 미입력 → 서버가 현재 년월(UTC)로 채운다
    expect = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
    assert c["first_registered_ym"] == expect

    # 편집 가능
    r = client.patch(f"/customers/{c['id']}", json={"first_registered_ym": "2023-05"})
    assert r.status_code == 200, r.text
    assert r.json()["first_registered_ym"] == "2023-05"
    assert client.get(f"/customers/{c['id']}").json()["first_registered_ym"] == "2023-05"

    # YYYY-MM-DD 도 허용
    assert client.patch(
        f"/customers/{c['id']}", json={"first_registered_ym": "2022-01-15"}
    ).status_code == 200

    # 형식 불량 → 422
    assert client.patch(
        f"/customers/{c['id']}", json={"first_registered_ym": "garbage"}
    ).status_code == 422

    # 생성 시 직접 지정도 가능
    c2 = client.post(
        "/customers", json={"name": "직접지정", "first_registered_ym": "2020-09"}
    ).json()
    assert c2["first_registered_ym"] == "2020-09"


def test_policyholder_fields_roundtrip(client, db_path):
    """계약자(피보험자와 다를 때) 이름/관계가 암호화 저장되고 복호화되어 나온다."""
    c = _make_customer(client)
    cid = c["id"]

    p = client.post(
        f"/customers/{cid}/policies",
        json={
            "insurer": "NH농협생명",
            "product_name": "NH올원더풀간병안심요양보험",
            "policyholder_name": "손유진",
            "policyholder_rel": "배우자",
        },
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    assert p.json()["policyholder_name"] == "손유진"
    assert p.json()["policyholder_rel"] == "배우자"

    # GET 으로도 복호화되어 나온다
    got = client.get(f"/customers/{cid}").json()["policies"][0]
    assert got["policyholder_name"] == "손유진" and got["policyholder_rel"] == "배우자"

    # 평문이 DB 파일에 남지 않는다
    raw = db_path.read_bytes()
    assert "손유진".encode("utf-8") not in raw

    # PATCH 로 관계만 변경
    r = client.patch(f"/policies/{pid}", json={"policyholder_rel": "자녀"})
    assert r.status_code == 200 and r.json()["policyholder_rel"] == "자녀"
    assert r.json()["policyholder_name"] == "손유진"  # 이름은 유지

    # PATCH 로 계약자 비우면 본인계약으로 되돌아간다
    r = client.patch(f"/policies/{pid}", json={"policyholder_name": ""})
    assert r.status_code == 200
    assert not r.json()["policyholder_name"]


def test_policyholder_rel_rejects_garbage(client):
    c = _make_customer(client)
    r = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "X생명", "product_name": "Y", "policyholder_rel": "사돈"},
    )
    assert r.status_code == 422


def test_policy_is_own_defaults_true_and_patchable(client):
    c = _make_customer(client)
    cid = c["id"]

    # is_own 미지정 → 기본 True
    p = client.post(
        f"/customers/{cid}/policies", json={"insurer": "ACME생명", "product_name": "기본"}
    ).json()
    assert p["is_own"] is True

    # 명시적 False
    p2 = client.post(
        f"/customers/{cid}/policies",
        json={"insurer": "타사", "product_name": "이관", "is_own": False},
    ).json()
    assert p2["is_own"] is False

    # PATCH 로 토글
    assert client.patch(f"/policies/{p['id']}", json={"is_own": False}).json()["is_own"] is False
    assert client.patch(f"/policies/{p2['id']}", json={"is_own": True}).json()["is_own"] is True


def test_customers_own_filter_and_dashboard_scope(client):
    a = _make_customer(client, name="내계약고객", phone="010-0000-0001")
    b = _make_customer(client, name="타사고객", phone="010-0000-0002")
    client.post(f"/customers/{a['id']}/policies",
                json={"insurer": "X", "product_name": "내것", "is_own": True})
    client.post(f"/customers/{b['id']}/policies",
                json={"insurer": "Y", "product_name": "남의것", "is_own": False})

    names = {c["name"] for c in client.get("/customers").json()["customers"]}
    assert {"내계약고객", "타사고객"} <= names

    own_names = {c["name"] for c in client.get("/customers?own=1").json()["customers"]}
    assert own_names == {"내계약고객"}

    d_all = client.get("/dashboard").json()
    d_own = client.get("/dashboard?own=1").json()
    assert d_all["counts"]["policies"] == 2
    assert d_own["counts"]["policies"] == 1
    assert d_all["counts"]["customers"] == d_own["counts"]["customers"] == 2  # 고객 수는 전역


def test_policy_lifecycle_and_link(client):
    c = _make_customer(client)
    cid = c["id"]

    r = client.post(
        f"/customers/{cid}/policies",
        json={
            "insurer": "ACME생명",
            "product_name": "스마트건강보험",
            "policy_number": "P-2026-001",
            "premium": 45000,
            "payment_cycle": "MONTHLY",
            "status": "ACTIVE",
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["customer_id"] == cid and p["premium"] == 45000

    # 고객 조회에 계약이 딸려 나온다
    detail = client.get(f"/customers/{cid}").json()
    assert len(detail["policies"]) == 1
    assert detail["policies"][0]["policy_number"] == "P-2026-001"

    # 계약 수정
    r = client.patch(f"/policies/{p['id']}", json={"status": "LAPSED", "premium": 50000})
    assert r.status_code == 200
    assert r.json()["status"] == "LAPSED" and r.json()["premium"] == 50000

    # 계약 삭제
    assert client.delete(f"/policies/{p['id']}").status_code == 204
    assert client.get(f"/customers/{cid}").json()["policies"] == []


@pytest.mark.parametrize(("premium", "expected"), [(-100, 422), (0, 201), (45000, 201), (None, 201)])
def test_policy_validates_nonnegative_premium(client, premium, expected):
    customer = _make_customer(client)
    payload = {"insurer": "보험사"}
    if premium is not None:
        payload["premium"] = premium
    assert client.post(
        f"/customers/{customer['id']}/policies", json=payload
    ).status_code == expected


def test_policy_patch_rejects_negative_premium(client):
    customer = _make_customer(client)
    policy = client.post(
        f"/customers/{customer['id']}/policies", json={"insurer": "보험사"}
    ).json()
    assert client.patch(f"/policies/{policy['id']}", json={"premium": -1}).status_code == 422


@pytest.mark.parametrize(
    ("status", "expected"),
    [("", 422), ("WEIRD", 422), ("ACTIVE", 201), ("LAPSED", 201)],
)
def test_policy_validates_status(client, status, expected):
    customer = _make_customer(client)
    assert client.post(
        f"/customers/{customer['id']}/policies",
        json={"insurer": "보험사", "status": status},
    ).status_code == expected


def test_policy_patch_rejects_empty_status(client):
    customer = _make_customer(client)
    policy = client.post(
        f"/customers/{customer['id']}/policies", json={"insurer": "보험사"}
    ).json()
    assert client.patch(f"/policies/{policy['id']}", json={"status": ""}).status_code == 422


def test_delete_customer_cascades_policies(client):
    c = _make_customer(client)
    cid = c["id"]
    p = client.post(f"/customers/{cid}/policies", json={"insurer": "X생명"}).json()

    assert client.delete(f"/customers/{cid}").status_code == 204
    assert client.get(f"/customers/{cid}").status_code == 404
    assert client.patch(f"/policies/{p['id']}", json={"memo": "x"}).status_code == 404


def test_not_found_paths(client):
    assert client.get("/customers/nope").status_code == 404
    assert client.patch("/customers/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/customers/nope").status_code == 404
    assert client.post("/customers/nope/policies", json={"insurer": "y"}).status_code == 404
    assert client.get("/customers/nope/policies").status_code == 404
    assert client.patch("/policies/nope", json={"memo": "z"}).status_code == 404
    assert client.delete("/policies/nope").status_code == 404


def test_create_customer_requires_name(client):
    assert client.post("/customers", json={"phone": "010"}).status_code == 422
    assert client.post("/customers", json={"name": ""}).status_code == 422


def test_pii_is_encrypted_at_rest(client, db_path):
    """DB 파일 원본 바이트에 이름/전화 평문이 남지 않아야 한다."""
    _make_customer(client, name="비밀고객", phone="010-9999-8888")
    client.post(
        f"/customers/{client.get('/customers').json()['customers'][0]['id']}/policies",
        json={"insurer": "숨김생명", "policy_number": "SECRET-777"},
    )

    raw = db_path.read_bytes()
    assert "비밀고객".encode("utf-8") not in raw
    assert b"010-9999-8888" not in raw
    assert b"SECRET-777" not in raw
    assert b"enc:v1:" in raw  # 암호문 마커는 있어야 정상

    # 그래도 API 로는 평문으로 잘 읽힌다
    c = client.get("/customers").json()["customers"][0]
    assert c["name"] == "비밀고객" and c["phone"] == "010-9999-8888"


def test_health_reports_encryption(client):
    body = client.get("/health").json()
    assert body["customer_db"]["encryption"].startswith("AES-256-GCM")
    assert body["customer_db"]["key_source"] == "env"


# ---------- 홈 대시보드 ----------

def test_dashboard_bundles_counts_followups_expiry_recent(client):
    import datetime

    cid = _make_customer(client, name="대시보드고객")["id"]
    soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    far = (datetime.date.today() + datetime.timedelta(days=300)).isoformat()

    # 만기 임박 계약 1 + 만기 먼 계약 1
    client.post(f"/customers/{cid}/policies", json={"insurer": "A생명", "end_date": soon})
    client.post(f"/customers/{cid}/policies", json={"insurer": "B화재", "end_date": far})
    # 후속 연락 예정 상담 1
    client.post(
        f"/customers/{cid}/consultations",
        json={"title": "재상담 예정", "follow_up_at": soon},
    )

    d = client.get("/dashboard").json()
    assert d["counts"]["customers"] == 1
    assert d["counts"]["policies"] == 2 and d["counts"]["active_policies"] == 2
    assert d["counts"]["consultations"] == 1

    assert len(d["follow_ups"]) == 1
    assert d["follow_ups"][0]["customer_name"] == "대시보드고객"

    assert [p["insurer"] for p in d["expiring_policies"]] == ["A생명"]  # far 계약은 제외
    assert d["expiring_policies"][0]["customer_name"] == "대시보드고객"

    assert len(d["recent_consultations"]) == 1
    assert "transcript" not in d["recent_consultations"][0]  # 무거워서 제외


def test_dashboard_empty(client):
    d = client.get("/dashboard").json()
    assert d["counts"] == {
        "customers": 0,
        "policies": 0,
        "active_policies": 0,
        "consultations": 0,
    }
    assert d["follow_ups"] == [] and d["expiring_policies"] == []
    assert d["recent_consultations"] == []


# ---------- 상담 이력 ----------

def test_consultation_lifecycle_and_nesting(client):
    cid = _make_customer(client)["id"]

    r = client.post(
        f"/customers/{cid}/consultations",
        json={"channel": "전화", "title": "가입 상담", "content": "암보험 관심"},
    )
    assert r.status_code == 201, r.text
    k = r.json()
    assert k["customer_id"] == cid
    assert k["consulted_at"]  # 생략 시 서버가 채움

    detail = client.get(f"/customers/{cid}").json()
    assert len(detail["consultations"]) == 1
    assert detail["consultations"][0]["title"] == "가입 상담"

    r = client.patch(f"/consultations/{k['id']}", json={"content": "암보험 가입 결정"})
    assert r.status_code == 200 and r.json()["content"] == "암보험 가입 결정"

    assert client.delete(f"/consultations/{k['id']}").status_code == 204
    assert client.get(f"/customers/{cid}").json()["consultations"] == []


def test_consultations_sorted_newest_first(client):
    cid = _make_customer(client)["id"]
    client.post(f"/customers/{cid}/consultations", json={"consulted_at": "2026-01-01", "title": "옛날"})
    client.post(f"/customers/{cid}/consultations", json={"consulted_at": "2026-08-01", "title": "최근"})
    titles = [c["title"] for c in client.get(f"/customers/{cid}/consultations").json()["consultations"]]
    assert titles == ["최근", "옛날"]


def test_consultation_content_encrypted_at_rest(client, db_path):
    cid = _make_customer(client)["id"]
    client.post(f"/customers/{cid}/consultations", json={"content": "민감한상담내용XYZ"})
    assert "민감한상담내용XYZ".encode("utf-8") not in db_path.read_bytes()


def test_follow_ups_endpoint(client):
    cid = _make_customer(client, name="후속고객")["id"]
    client.post(
        f"/customers/{cid}/consultations",
        json={"title": "연락요망", "follow_up_at": "2026-01-15"},
    )
    body = client.get("/follow-ups", params={"until": "2026-02-01"}).json()
    assert len(body["follow_ups"]) == 1
    assert body["follow_ups"][0]["customer_name"] == "후속고객"

    # 범위 밖
    assert client.get("/follow-ups", params={"until": "2026-01-01"}).json()["follow_ups"] == []


def test_consultations_cascade_on_customer_delete(client):
    cid = _make_customer(client)["id"]
    k = client.post(f"/customers/{cid}/consultations", json={"title": "x"}).json()
    client.delete(f"/customers/{cid}")
    assert client.patch(f"/consultations/{k['id']}", json={"title": "y"}).status_code == 404


# ---------- 고객 ↔ 약관 연결 ----------

POLICY_PAGES = [
    "Article 1. Contract between ACME Life and the insured.",
    "Article 5 (Hospitalization benefit). Pays 30,000 KRW per day of hospitalization.",
    "Article 6 (Surgery benefit). Pays a lump sum for a covered surgical operation.",
]


@pytest.fixture
def rag_mocked(monkeypatch):
    from rag import answerer, pipeline
    from rag.embedder import HashingEmbedder

    monkeypatch.setattr(pipeline, "get_embedder", lambda: HashingEmbedder())
    monkeypatch.setattr(
        answerer,
        "_call_llm",
        lambda *a, **k: {
            "answer": "입원 1일당 30,000원을 지급합니다.",
            "company": "ACME Life",
            "product": None,
            "clause": "Article 5",
            "grounded": True,
        },
    )


def test_attach_document_and_ask_scoped(client, build_pdf, rag_mocked):
    cid = _make_customer(client)["id"]
    pid = client.post(f"/customers/{cid}/policies", json={"insurer": "ACME"}).json()["id"]

    r = client.post(
        f"/policies/{pid}/document",
        files={"file": ("acme.pdf", build_pdf(POLICY_PAGES), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["indexed"]["chunks_indexed"] >= 3
    linked_doc = r.json()["policy"]["document_id"]
    assert linked_doc

    detail = client.get(f"/customers/{cid}").json()
    assert detail["policies"][0]["document_id"] == linked_doc

    # HashingEmbedder 는 언어 간 매칭이 안 되므로 영문 PDF엔 영문 질의
    ans = client.get(
        f"/customers/{cid}/ask", params={"q": "hospitalization benefit per day", "top_k": 3}
    )
    assert ans.status_code == 200
    body = ans.json()
    assert body["grounded"] is True
    assert body["clause"] == "Article 5"
    assert linked_doc in body["document_ids"]
    assert body["sources"] and all(s["doc_id"] == linked_doc for s in body["sources"])


def test_ask_with_no_linked_documents_abstains(client, rag_mocked):
    cid = _make_customer(client)["id"]
    body = client.get(f"/customers/{cid}/ask", params={"q": "아무거나"}).json()
    assert body["abstained"] is True
    assert body["document_ids"] == []
    assert "연결된 약관이 없습니다" in body["answer"]


RRN = "9001011234567"          # 형식만 유효한 더미 (900101 / 성별자리 1)
RRN_MASKED = "900101-1******"


def test_rrn_stored_encrypted_returned_in_full(client, db_path, rrn_on):
    """마스킹 해제(사용자 요청): 응답엔 전체값이 나오되 저장은 여전히 암호화."""
    r = client.post("/customers", json={"name": "주민번호고객", "rrn": "900101-1234567"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_rrn"] is True
    assert body["rrn"] == RRN and "rrn_hash" not in body  # 전체값 노출, 지문은 안 나감

    detail = client.get(f"/customers/{body['id']}").json()
    assert detail["rrn"] == RRN  # 상세에선 전체값
    listed = client.get("/customers").json()["customers"][0]
    assert listed["has_rrn"] is True and listed["rrn"] is None  # 목록엔 전체값 안 실음

    # 저장은 여전히 암호화 — 원본 바이트에 주민번호 숫자열이 없어야 한다
    raw = db_path.read_bytes()
    assert RRN.encode() not in raw
    assert b"900101-1234567" not in raw


def test_rrn_invalid_format_rejected(client, rrn_on):
    assert client.post("/customers", json={"name": "x", "rrn": "123"}).status_code == 422
    assert client.post("/customers", json={"name": "x", "rrn": "9013011234567"}).status_code == 422  # 13월
    assert client.post("/customers", json={"name": "x", "rrn": "9001010234567"}).status_code == 422  # 성별자리 0


def test_rrn_rejects_invalid_calendar_date(client, rrn_on):
    assert client.post(
        "/customers", json={"name": "달력 오류", "rrn": "9002311234567"}
    ).status_code == 422


def test_rrn_accepts_2000_leap_day(client, rrn_on):
    created = client.post(
        "/customers", json={"name": "윤년", "rrn": "0002293234567"}
    )
    assert created.status_code == 201
    assert created.json()["birth_date"] == "2000-02-29"


def test_rrn_reveal_writes_access_log(client, rrn_on):
    cid = client.post("/customers", json={"name": "조회대상", "rrn": RRN}).json()["id"]

    assert client.get(f"/customers/{cid}/rrn").status_code == 422  # purpose 필수

    r = client.get(
        f"/customers/{cid}/rrn",
        params={"purpose": "보험금 청구 확인"},
        headers={"X-Actor-Id": "agent-7"},
    )
    assert r.status_code == 200
    assert r.json()["rrn"] == RRN
    assert r.json()["rrn_masked"] == RRN_MASKED

    log = client.get(f"/customers/{cid}/rrn-access-log").json()["access_log"]
    reveal_log = [row for row in log if row["access_type"] == "explicit_reveal"]
    assert len(reveal_log) == 1
    assert reveal_log[0]["purpose"] == "보험금 청구 확인" and reveal_log[0]["accessed_at"]
    assert reveal_log[0]["actor"] == "agent-7"


def test_rrn_customer_detail_writes_access_log(client, rrn_on):
    cid = client.post("/customers", json={"name": "상세조회", "rrn": RRN}).json()["id"]

    detail = client.get(
        f"/customers/{cid}", headers={"X-Actor-Id": "advisor-1"}
    )
    assert detail.status_code == 200
    assert detail.json()["rrn"] == RRN

    log = client.get(f"/customers/{cid}/rrn-access-log").json()["access_log"]
    detail_log = [row for row in log if row["access_type"] == "detail_open"]
    assert len(detail_log) == 1
    assert detail_log[0]["purpose"] == "고객 상세 화면 열람"
    assert detail_log[0]["actor"] == "advisor-1"
    assert detail_log[0]["accessed_at"]


def test_rrn_customer_detail_does_not_log_when_toggle_off(client, db_path, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "1")
    cid = client.post("/customers", json={"name": "토글대상", "rrn": RRN}).json()["id"]
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")

    detail = client.get(f"/customers/{cid}", headers={"X-Actor-Id": "advisor-2"})
    assert detail.status_code == 200
    assert detail.json()["rrn"] is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM rrn_access_log "
            "WHERE customer_id = ? AND access_type = 'detail_open'", (cid,)
        ).fetchone()[0] == 0


def test_customer_detail_without_rrn_does_not_log(client, rrn_on):
    cid = _make_customer(client, name="주민번호없음")["id"]

    detail = client.get(f"/customers/{cid}", headers={"X-Actor-Id": "advisor-3"})
    assert detail.status_code == 200
    assert detail.json()["rrn"] is None
    assert client.get(f"/customers/{cid}/rrn-access-log").json()["access_log"] == []


def test_rrn_create_response_writes_access_log(client, rrn_on):
    r = client.post(
        "/customers",
        json={"name": "생성응답", "rrn": RRN},
        headers={"X-Actor-Id": "creator-1"},
    )
    assert r.status_code == 201 and r.json()["rrn"] == RRN

    log = client.get(f"/customers/{r.json()['id']}/rrn-access-log").json()["access_log"]
    assert len(log) == 1
    assert log[0]["access_type"] == "create_response"
    assert log[0]["actor"] == "creator-1"
    assert log[0]["purpose"] == "고객 저장 응답에 주민등록번호 포함"
    assert log[0]["accessed_at"]


def test_rrn_update_response_writes_access_log(client, rrn_on):
    cid = _make_customer(client, name="수정응답")["id"]
    r = client.patch(
        f"/customers/{cid}",
        json={"rrn": RRN},
        headers={"X-Actor-Id": "updater-1"},
    )
    assert r.status_code == 200 and r.json()["rrn"] == RRN

    log = client.get(f"/customers/{cid}/rrn-access-log").json()["access_log"]
    assert len(log) == 1
    assert log[0]["access_type"] == "update_response"
    assert log[0]["actor"] == "updater-1"
    assert log[0]["purpose"] == "고객 저장 응답에 주민등록번호 포함"


def test_rrn_save_responses_do_not_log_without_exposed_rrn(client, db_path, monkeypatch):
    monkeypatch.setenv("RRN_INPUT_ENABLED", "0")
    created = client.post(
        "/customers", json={"name": "토글꺼짐", "rrn": RRN}
    )
    assert created.status_code == 201 and created.json()["rrn"] is None
    cid = created.json()["id"]

    patched = client.patch(f"/customers/{cid}", json={"memo": "전체값 없음"})
    assert patched.status_code == 200 and patched.json()["rrn"] is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM rrn_access_log WHERE customer_id = ?", (cid,)
        ).fetchone()[0] == 0


def test_focus_customer_uses_selected_id_detail_request_only():
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "desktop" / "src" / "Customers.tsx"
    ).read_text()
    focus_effect = source.split(
        '// 홈 대시보드 / 던져넣기 등에서 고객을 눌러 들어온 경우.', 1
    )[1].split("useEffect(() => {", 2)[1]
    assert "setSelectedId(focusCustomer.id)" in focus_effect
    assert "openDetail(focusCustomer.id)" not in focus_effect


def test_rrn_reveal_404_when_absent(client, rrn_on):
    cid = _make_customer(client)["id"]  # rrn 없음
    assert client.get(f"/customers/{cid}/rrn", params={"purpose": "확인"}).status_code == 404


def test_duplicate_rrn_rejected(client, rrn_on):
    client.post("/customers", json={"name": "먼저", "rrn": RRN})
    dup = client.post("/customers", json={"name": "나중", "rrn": "900101-1234567"})
    assert dup.status_code == 409


def test_rrn_patch_and_clear(client, rrn_on):
    cid = client.post("/customers", json={"name": "수정대상", "rrn": RRN}).json()["id"]
    r = client.patch(f"/customers/{cid}", json={"rrn": "8512121234561"})
    assert r.status_code == 200 and r.json()["rrn"] == "8512121234561"
    # 빈 문자열로 삭제
    r = client.patch(f"/customers/{cid}", json={"rrn": ""})
    assert r.status_code == 200 and r.json()["has_rrn"] is False and r.json()["rrn"] is None


def test_rrn_sets_birthdate_and_gender_on_create(client, rrn_on):
    d = client.post("/customers", json={"name": "홍", "rrn": "9102011234567"}).json()
    assert d["birth_date"] == "1991-02-01"
    assert d["gender"] == "M"
    assert d["birth_date_estimated"] is False


def test_rrn_patch_realigns_stale_birthdate(client, rrn_on):
    cid = client.post("/customers", json={"name": "홍", "birth_date": "1945-06-21"}).json()["id"]
    d = client.patch(f"/customers/{cid}", json={"rrn": "9102011234567"}).json()
    assert d["birth_date"] == "1991-02-01"   # 주민번호에 맞춰 교정
    assert d["gender"] == "M"


def test_explicit_birthdate_wins_over_rrn_when_sent_together(client, rrn_on):
    d = client.post(
        "/customers",
        json={"name": "홍", "rrn": "9102011234567", "birth_date": "1990-12-25"},
    ).json()
    assert d["birth_date"] == "1990-12-25"   # 같이 보낸 명시값은 존중


def test_detach_document(client, build_pdf, rag_mocked):
    cid = _make_customer(client)["id"]
    pid = client.post(f"/customers/{cid}/policies", json={"insurer": "ACME"}).json()["id"]
    client.post(
        f"/policies/{pid}/document",
        files={"file": ("acme.pdf", build_pdf(POLICY_PAGES), "application/pdf")},
    )
    assert client.delete(f"/policies/{pid}/document").status_code == 204
    detail = client.get(f"/customers/{cid}").json()
    assert detail["policies"][0]["document_id"] is None


# ---------- 보장 분석 ----------

def test_coverage_analysis_no_policies(client):
    cid = _make_customer(client)["id"]
    r = client.get(f"/customers/{cid}/coverage-analysis")
    assert r.status_code == 200
    b = r.json()
    assert b["analyzed_policies"] == 0
    assert b["categories"] == [] and "보험계약이 없어" in b["overall"]


def test_coverage_analysis_with_policies(client, monkeypatch):
    from database import coverage

    monkeypatch.setattr(coverage, "rag_search", lambda *a, **k: {"hits": []})
    monkeypatch.setattr(
        coverage,
        "_call_llm",
        lambda *a, **k: {
            "overall": "암 보장은 있으나 실손이 없습니다.",
            "categories": [
                {"name": "암 진단", "status": "충분", "detail": "A생명 5천만원",
                 "policies": ["건강보험"], "evidence_pages": [3]},
                {"name": "실손의료", "status": "없음", "detail": "가입 없음",
                 "policies": [], "evidence_pages": []},
            ],
            "gaps": ["실손의료보험 미가입"],
            "overlaps": [],
            "recommendations": ["실손 신규 가입 검토"],
        },
    )

    cid = _make_customer(client)["id"]
    client.post(f"/customers/{cid}/policies", json={"insurer": "A생명", "product_name": "건강보험"})

    r = client.get(f"/customers/{cid}/coverage-analysis")
    assert r.status_code == 200
    b = r.json()
    assert b["analyzed_policies"] == 1
    assert b["has_documents"] is False
    assert [c["name"] for c in b["categories"]] == ["암 진단", "실손의료"]
    assert b["gaps"] == ["실손의료보험 미가입"]
    assert b["recommendations"] == ["실손 신규 가입 검토"]


def test_coverage_analysis_404(client):
    assert client.get("/customers/nope/coverage-analysis").status_code == 404


# ---------- 태그 + 목록 강화 (정렬/필터/배지) ----------

def test_tags_roundtrip_and_listing(client):
    c = client.post(
        "/customers", json={"name": "태그고객", "tags": ["VIP", "암보험관심", "VIP"]}
    ).json()
    assert c["tags"] == ["VIP", "암보험관심"]  # 중복 제거, 순서 유지

    d = client.get(f"/customers/{c['id']}").json()
    assert d["tags"] == ["VIP", "암보험관심"]

    client.patch(f"/customers/{c['id']}", json={"tags": ["신규"]})
    assert client.get(f"/customers/{c['id']}").json()["tags"] == ["신규"]

    _make_customer(client, name="다른고객")
    client.patch(
        f"/customers/{client.get('/customers', params={'q': '다른'}).json()['customers'][0]['id']}",
        json={"tags": ["신규", "소개"]},
    )
    assert client.get("/tags").json()["tags"] == ["소개", "신규"]


def test_tag_encrypted_at_rest(client, db_path):
    client.post("/customers", json={"name": "x", "tags": ["기밀태그ABC"]})
    assert "기밀태그ABC".encode("utf-8") not in db_path.read_bytes()


def test_list_enrichment_fields(client):
    import datetime

    cid = _make_customer(client, name="풍부한고객")["id"]
    soon = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    client.post(f"/customers/{cid}/policies", json={"insurer": "A", "end_date": soon})
    client.post(f"/customers/{cid}/policies", json={"insurer": "B"})  # end_date 없음
    client.post(
        f"/customers/{cid}/consultations", json={"title": "t", "follow_up_at": soon}
    )

    row = client.get("/customers", params={"q": "풍부한"}).json()["customers"][0]
    assert row["policy_count"] == 2 and row["active_policy_count"] == 2
    assert row["soonest_expiry"] == soon
    assert row["next_follow_up"] == soon
    assert row["last_consulted_at"]


def test_list_filters_and_sort(client):
    import datetime

    today = datetime.date.today()
    a = _make_customer(client, name="AAA")["id"]
    b = _make_customer(client, name="BBB")["id"]
    client.patch(f"/customers/{a}", json={"tags": ["급한"]})
    client.post(
        f"/customers/{a}/policies",
        json={"insurer": "A", "end_date": (today + datetime.timedelta(days=5)).isoformat()},
    )
    client.post(
        f"/customers/{b}/policies",
        json={"insurer": "B", "end_date": (today + datetime.timedelta(days=200)).isoformat()},
    )
    client.post(
        f"/customers/{b}/consultations",
        json={"title": "x", "follow_up_at": (today + datetime.timedelta(days=2)).isoformat()},
    )

    # tag 필터
    names = [c["name"] for c in client.get("/customers", params={"tag": "급한"}).json()["customers"]]
    assert names == ["AAA"]

    # 만기 임박 필터 (30일)
    names = [c["name"] for c in client.get("/customers", params={"expiring_days": 30}).json()["customers"]]
    assert names == ["AAA"]

    # 후속 연락 있는 고객만
    names = [c["name"] for c in client.get("/customers", params={"has_follow_up": "true"}).json()["customers"]]
    assert names == ["BBB"]

    # 만기 빠른 순 정렬
    names = [c["name"] for c in client.get("/customers", params={"sort": "expiry"}).json()["customers"]]
    assert names[:2] == ["AAA", "BBB"]
