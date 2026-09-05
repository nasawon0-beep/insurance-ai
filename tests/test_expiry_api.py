"""
큰 기능 #2 백엔드 API 테스트:
만기 자동 산출(B3) / 고객 상태 하이브리드(B4) / 후속 연락 완료 + 생일(B5) /
엔드포인트 (/management, /dashboard 확장, follow-up 완료, 백필)(B6).
"""
import base64
import datetime as dt
import sys
from pathlib import Path

import pytest

pytest.importorskip("httpx")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "rag.sqlite3"))
    crypto.reset_cache()
    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def _today():
    return dt.date.today()


def _mk(client, **kw):
    body = {"name": "고객"}
    body.update(kw)
    r = client.post("/customers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ===================== B3: 만기 자동 산출 =====================

def test_insured_period_only_autocomputes_end_date(client):
    c = _mk(client, birth_date="1980-05-10")
    p = client.post(
        f"/customers/{c['id']}/policies", json={"insurer": "A", "insured_period": "100세"}
    ).json()
    assert p["end_date"] == "2080-05-10"
    assert p["end_date_derived"] == 1


def test_explicit_end_date_is_not_touched_by_insured_period(client):
    c = _mk(client, birth_date="1980-05-10")
    p = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "A", "end_date": "2099-12-31", "insured_period": "20년",
              "start_date": "2020-01-01"},
    ).json()
    assert p["end_date"] == "2099-12-31" and p["end_date_derived"] == 0

    # 나중에 insured_period / start_date 를 바꿔도 수동 만기는 불변
    p2 = client.patch(
        f"/policies/{p['id']}", json={"insured_period": "10년", "start_date": "2021-01-01"}
    ).json()
    assert p2["end_date"] == "2099-12-31" and p2["end_date_derived"] == 0


def test_clearing_end_date_keeps_manual_empty_on_later_period_change(client):
    c = _mk(client)
    p = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "A", "insured_period": "20년", "start_date": "2020-03-15"},
    ).json()
    assert p["end_date"] == "2040-03-15" and p["end_date_derived"] == 1

    p = client.patch(f"/policies/{p['id']}", json={"end_date": "2099-01-01"}).json()
    assert p["end_date"] == "2099-01-01" and p["end_date_derived"] == 0

    p = client.patch(f"/policies/{p['id']}", json={"end_date": ""}).json()
    assert p["end_date"] is None and p["end_date_derived"] == 0

    p = client.patch(f"/policies/{p['id']}", json={"insured_period": "10년"}).json()
    assert p["end_date"] is None and p["end_date_derived"] == 0

    p2 = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "B", "insured_period": "20년", "start_date": "2020-03-15"},
    ).json()
    p2 = client.patch(f"/policies/{p2['id']}", json={"end_date": None}).json()
    assert p2["end_date"] is None and p2["end_date_derived"] == 0


def test_period_change_recomputes_derived_end_date_and_normalizes_start_date(client):
    c = _mk(client)
    p = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "A", "insured_period": "20년", "start_date": "2020-03-15"},
    ).json()
    p = client.patch(
        f"/policies/{p['id']}", json={"insured_period": "10년", "start_date": "20200101"}
    ).json()
    assert p["start_date"] == "2020-01-01"
    assert p["end_date"] == "2030-01-01" and p["end_date_derived"] == 1


def test_changing_customer_birthdate_updates_age_type_policies(client):
    c = _mk(client)  # 생년월일 없음
    p = client.post(
        f"/customers/{c['id']}/policies", json={"insurer": "A", "insured_period": "90세"}
    ).json()
    assert p["end_date"] is None  # 생년월일 없어 계산 불가

    client.patch(f"/customers/{c['id']}", json={"birth_date": "1970-06-15"})
    p = client.get(f"/customers/{c['id']}/policies").json()["policies"][0]
    assert p["end_date"] == "2060-06-15" and p["end_date_derived"] == 1


def test_payment_end_date_autocomputed(client):
    c = _mk(client, birth_date="1980-05-10")
    p = client.post(
        f"/customers/{c['id']}/policies",
        json={"insurer": "A", "start_date": "2020-01-01", "insured_period": "100세",
              "payment_period": "20년납"},
    ).json()
    assert p["payment_end_date"] == "2040-01-01"


# ===================== B4: 고객 상태 (하이브리드) =====================

def test_new_customer_defaults_to_prospect(client):
    c = _mk(client)
    assert c["customer_status"] == "가망"
    assert client.get(f"/customers/{c['id']}").json()["effective_status"] == "가망"


def test_customer_status_accepts_all_four_and_rejects_garbage(client):
    for s in ("가입", "미가입", "가망", "해지"):
        assert client.post("/customers", json={"name": "x", "customer_status": s}).status_code == 201
    assert client.post("/customers", json={"name": "x", "customer_status": "기타"}).status_code == 422


def test_active_policy_bumps_status_to_subscribed(client):
    c = _mk(client)  # 기본 '가망'
    client.post(f"/customers/{c['id']}/policies", json={"insurer": "A", "status": "ACTIVE"})
    d = client.get(f"/customers/{c['id']}").json()
    assert d["customer_status"] == "가입" and d["effective_status"] == "가입"
    row = client.get("/customers", params={"q": "고객"}).json()["customers"][0]
    assert row["effective_status"] == "가입"


def test_manual_status_is_respected(client):
    c = _mk(client)
    client.patch(f"/customers/{c['id']}", json={"customer_status": "해지"})
    assert client.get(f"/customers/{c['id']}").json()["effective_status"] == "해지"


def test_sole_policy_cancelled_stamps_terminated(client):
    c = _mk(client)
    p = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A", "status": "ACTIVE"}).json()
    client.patch(f"/policies/{p['id']}", json={"status": "CANCELLED"})
    d = client.get(f"/customers/{c['id']}").json()
    assert d["customer_status"] == "해지"
    assert d["effective_status"] == "해지"


def test_deleting_last_policy_reverts_autobumped_status_to_prospect(client):
    c = _mk(client)
    p = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A", "status": "ACTIVE"}).json()
    # 계약 추가로 '가입' 이 됨
    assert client.get(f"/customers/{c['id']}").json()["customer_status"] == "가입"
    assert client.delete(f"/policies/{p['id']}").status_code == 204
    d = client.get(f"/customers/{c['id']}").json()
    # 계약이 하나도 안 남았고 상태가 자동으로 올라간 '가입' 이었으니 '가망' 으로 되돌린다
    assert d["customer_status"] == "가망"
    assert d["effective_status"] == "가망"


def test_deleting_last_policy_keeps_manual_status(client):
    c = _mk(client)
    p = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A", "status": "ACTIVE"}).json()
    client.patch(f"/customers/{c['id']}", json={"customer_status": "해지"})  # 사용자가 직접 지정
    assert client.delete(f"/policies/{p['id']}").status_code == 204
    # 직접 정한 상태는 삭제해도 건드리지 않는다 (자동으로 올라간 '가입' 만 되돌림)
    assert client.get(f"/customers/{c['id']}").json()["customer_status"] == "해지"


def test_deleting_one_of_two_policies_keeps_subscribed(client):
    c = _mk(client)
    p1 = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A"}).json()
    client.post(f"/customers/{c['id']}/policies", json={"insurer": "B"})
    assert client.delete(f"/policies/{p1['id']}").status_code == 204
    # 계약이 아직 남아 있으면 '가입' 유지
    assert client.get(f"/customers/{c['id']}").json()["customer_status"] == "가입"


def test_empty_policy_is_rejected(client):
    c = _mk(client)
    r = client.post(f"/customers/{c['id']}/policies", json={})
    assert r.status_code == 422
    # 빈 계약이 거부됐으니 상태도 '가망' 그대로
    assert client.get(f"/customers/{c['id']}").json()["customer_status"] == "가망"
    assert client.get(f"/customers/{c['id']}").json()["policies"] == []


def test_one_of_two_policies_cancelled_still_subscribed(client):
    c = _mk(client)
    p1 = client.post(f"/customers/{c['id']}/policies", json={"insurer": "A"}).json()
    client.post(f"/customers/{c['id']}/policies", json={"insurer": "B"})
    client.patch(f"/policies/{p1['id']}", json={"status": "EXPIRED"})
    d = client.get(f"/customers/{c['id']}").json()
    # 활성 계약(B) 남아 있어 '해지' 스탬프 안 됨 → 계약추가 때 올라간 '가입' 유지
    assert d["customer_status"] == "가입" and d["effective_status"] == "가입"


def test_status_filter_on_customer_list(client):
    a = _mk(client, name="가입자")
    client.post(f"/customers/{a['id']}/policies", json={"insurer": "A"})  # → '가입'
    _mk(client, name="가망자")

    subs = client.get("/customers", params={"status": "가입"}).json()["customers"]
    assert [x["name"] for x in subs] == ["가입자"]
    pros = client.get("/customers", params={"status": "가망"}).json()["customers"]
    assert [x["name"] for x in pros] == ["가망자"]


def test_birth_date_estimated_roundtrip(client):
    c = _mk(client, birth_date="1990-01-01", birth_date_estimated=True)
    assert c["birth_date_estimated"] is True
    assert client.get(f"/customers/{c['id']}").json()["birth_date_estimated"] is True
    client.patch(f"/customers/{c['id']}", json={"birth_date_estimated": False})
    assert client.get(f"/customers/{c['id']}").json()["birth_date_estimated"] is False


# ===================== B5: 후속 연락 완료 + 생일 =====================

def test_follow_up_complete_and_reopen(client):
    c = _mk(client)
    soon = (_today() + dt.timedelta(days=2)).isoformat()
    k = client.post(f"/customers/{c['id']}/consultations",
                    json={"title": "재상담", "content": "원본", "follow_up_at": soon}).json()

    assert len(client.get("/follow-ups", params={"until": (_today() + dt.timedelta(days=7)).isoformat()}).json()["follow_ups"]) == 1

    r = client.post(f"/consultations/{k['id']}/follow-up/complete")
    assert r.status_code == 200
    assert r.json()["follow_up_done_at"]
    assert r.json()["content"] == "원본"

    assert client.get("/follow-ups", params={"until": (_today() + dt.timedelta(days=7)).isoformat()}).json()["follow_ups"] == []
    assert client.get("/dashboard").json()["follow_ups"] == []

    r = client.post(f"/consultations/{k['id']}/follow-up/reopen")
    assert r.status_code == 200 and r.json()["follow_up_done_at"] is None
    assert len(client.get("/follow-ups", params={"until": (_today() + dt.timedelta(days=7)).isoformat()}).json()["follow_ups"]) == 1


def test_follow_up_complete_reopen_is_symmetric(client):
    c = _mk(client)
    original = "첫 줄\n\n마지막 줄"
    utc_today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    k = client.post(
        f"/customers/{c['id']}/consultations", json={"content": original}
    ).json()

    for _ in range(2):
        completed = client.post(f"/consultations/{k['id']}/follow-up/complete").json()
        assert completed["follow_up_done_at"] == utc_today
        assert completed["content"] == original

        reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()
        assert reopened["follow_up_done_at"] is None
        assert reopened["content"] == original


def test_reopen_removes_only_legacy_done_marker_lines(client):
    from database import db
    from database.crypto import get_cipher

    c = _mk(client)
    k = client.post(f"/customers/{c['id']}/consultations", json={"content": "temp"}).json()
    legacy = "사용자 첫 줄\n[2025-01-02] 후속 연락 완료\n\n\n[2025-03-04] 후속 연락 완료  \n사용자 마지막 줄"
    with db.connect() as conn:
        conn.execute(
            "UPDATE consultations SET content = ?, follow_up_done_at = ? WHERE id = ?",
            (get_cipher().encrypt(legacy), "2025-03-04", k["id"]),
        )

    reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()
    assert reopened["follow_up_done_at"] is None
    assert reopened["content"] == "사용자 첫 줄\n\n\n사용자 마지막 줄"


def test_reopen_without_marker_preserves_content_and_ciphertext(client):
    from database import db

    c = _mk(client)
    content = "  선행공백\n\n\n연속개행  "
    k = client.post(
        f"/customers/{c['id']}/consultations", json={"content": content}
    ).json()
    client.post(f"/consultations/{k['id']}/follow-up/complete")

    with db.connect() as conn:
        before = conn.execute(
            "SELECT content FROM consultations WHERE id = ?", (k["id"],)
        ).fetchone()["content"]

    reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()

    with db.connect() as conn:
        after = conn.execute(
            "SELECT content FROM consultations WHERE id = ?", (k["id"],)
        ).fetchone()["content"]
    assert reopened["content"] == content
    assert after == before


def test_reopen_removes_crlf_done_marker_line(client):
    from database import db
    from database.crypto import get_cipher

    c = _mk(client)
    k = client.post(f"/customers/{c['id']}/consultations", json={"content": "temp"}).json()
    content = "첫줄\r\n[2025-01-02] 후속 연락 완료\r\n끝"
    with db.connect() as conn:
        conn.execute(
            "UPDATE consultations SET content = ?, follow_up_done_at = ? WHERE id = ?",
            (get_cipher().encrypt(content), "2025-01-02", k["id"]),
        )

    reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()
    assert reopened["content"] == "첫줄\r\n끝"


def test_reopen_marker_only_content_becomes_empty(client):
    c = _mk(client)
    k = client.post(
        f"/customers/{c['id']}/consultations",
        json={"content": "[2025-01-02] 후속 연락 완료"},
    ).json()

    reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()
    assert reopened["content"] == ""


def test_reopen_preserves_user_text_that_only_starts_like_marker(client):
    c = _mk(client)
    content = "[2026-01-01] 후속 연락 완료 — 내가 덧붙인 메모"
    k = client.post(
        f"/customers/{c['id']}/consultations",
        json={"content": content, "follow_up_done_at": "2026-01-01"},
    ).json()

    reopened = client.post(f"/consultations/{k['id']}/follow-up/reopen").json()
    assert reopened["follow_up_done_at"] is None
    assert reopened["content"] == content


def test_new_follow_up_date_reopens(client):
    c = _mk(client)
    past = (_today() - dt.timedelta(days=1)).isoformat()
    k = client.post(f"/customers/{c['id']}/consultations", json={"follow_up_at": past}).json()
    client.post(f"/consultations/{k['id']}/follow-up/complete")
    fut = (_today() + dt.timedelta(days=5)).isoformat()
    k2 = client.patch(f"/consultations/{k['id']}", json={"follow_up_at": fut}).json()
    assert k2["follow_up_done_at"] is None


def test_management_splits_overdue_follow_ups(client):
    c = _mk(client)
    client.post(f"/customers/{c['id']}/consultations",
                json={"title": "연체", "follow_up_at": (_today() - dt.timedelta(days=5)).isoformat()})
    client.post(f"/customers/{c['id']}/consultations",
                json={"title": "예정", "follow_up_at": (_today() + dt.timedelta(days=3)).isoformat()})
    m = client.get("/management").json()
    assert set(m) == {"expiring_policies", "payment_ending", "birthdays", "follow_ups", "overdue_follow_ups"}
    assert [f["title"] for f in m["overdue_follow_ups"]] == ["연체"]
    assert [f["title"] for f in m["follow_ups"]] == ["예정"]


def test_upcoming_birthdays_in_window_and_estimated_flag(client):
    bday = _today() + dt.timedelta(days=10)
    _mk(client, name="곧생일", birth_date=f"1990-{bday.month:02d}-{bday.day:02d}",
        birth_date_estimated=True)
    passed = _today() - dt.timedelta(days=20)
    _mk(client, name="지난생일", birth_date=f"1985-{passed.month:02d}-{passed.day:02d}")

    d = client.get("/dashboard").json()
    assert d["birthday_until"]
    names = [b["name"] for b in d["birthdays"]]
    assert "곧생일" in names and "지난생일" not in names
    entry = next(b for b in d["birthdays"] if b["name"] == "곧생일")
    assert entry["estimated"] is True and entry["days_until"] == 10
    assert entry["turning_age"] == bday.year - 1990


def test_birthday_view_helper_feb29_and_wraparound():
    from database import repo

    v = repo._birthday_view("2000-02-29", dt.date(2025, 2, 1))
    assert v["mmdd"] == "02-29" and v["next"] == dt.date(2025, 2, 28)  # 평년 클램프
    assert v["turning_age"] == 25

    v2 = repo._birthday_view("1990-01-05", dt.date(2025, 6, 1))  # 이미 지남 → 내년
    assert v2["next"] == dt.date(2026, 1, 5) and v2["days_until"] > 200

    assert repo._birthday_view("not-a-date") is None
    assert repo._birthday_view("1990-13-40") is None


# ===================== B6: 백필 엔드포인트 =====================

def test_recompute_expiry_fills_from_memo_and_preserves_manual(client):
    c1 = _mk(client, name="갑", birth_date="1980-01-01")
    # insured_period 없이 memo 에만 기간이 적힌 계약
    p1 = client.post(f"/customers/{c1['id']}/policies", json={
        "insurer": "A", "start_date": "2020-01-01",
        "memo": "보험기간 20년 · 납입기간 20년납",
    }).json()
    assert p1["end_date"] is None  # 생성 시엔 insured_period 없어 계산 안 됨

    # 수동으로 만기 직접입력한 계약 (백필이 건드리면 안 됨)
    p2 = client.post(f"/customers/{c1['id']}/policies", json={
        "insurer": "B", "end_date": "2035-01-01", "memo": "보험기간 30년",
    }).json()
    assert p2["end_date_derived"] == 0

    # 생년월일 없는 N세형 → need_birthdate
    c2 = _mk(client, name="을")
    p3 = client.post(f"/customers/{c2['id']}/policies",
                     json={"insurer": "C", "insured_period": "100세"}).json()

    res = client.post("/maintenance/recompute-expiry").json()
    assert res["scanned"] == 3
    assert res["skipped_manual"] == 1
    assert res["filled_from_memo"] == 1
    assert res["updated"] >= 1
    assert res["failed"] == []
    nb = res["need_birthdate"]
    assert len(nb) == 1
    assert nb[0]["policy_id"] == p3["id"] and nb[0]["customer_id"] == c2["id"]
    assert nb[0]["customer_name"] == "을" and nb[0]["insured_period"] == "100세"

    # p1 은 memo 기반으로 채워짐
    got1 = client.get(f"/customers/{c1['id']}/policies").json()["policies"]
    p1n = next(p for p in got1 if p["id"] == p1["id"])
    p2n = next(p for p in got1 if p["id"] == p2["id"])
    assert p1n["insured_period"] == "20년"
    assert p1n["end_date"] == "2040-01-01" and p1n["end_date_derived"] == 1
    assert p1n["payment_end_date"] == "2040-01-01"
    assert p2n["end_date"] == "2035-01-01"  # 수동 만기 보존

    # 멱등: 두 번째 실행은 아무것도 새로 바꾸지 않음
    res2 = client.post("/maintenance/recompute-expiry").json()
    assert res2["scanned"] == 3 and res2["updated"] == 0
    assert res2["filled_from_memo"] == 0 and res2["skipped_manual"] == 1
    assert len(res2["need_birthdate"]) == 1


def test_dashboard_expiring_policy_carries_derived_meta(client):
    c = _mk(client, birth_date="1980-05-10")
    soon = (_today() + dt.timedelta(days=10)).isoformat()
    client.post(f"/customers/{c['id']}/policies", json={"insurer": "A", "end_date": soon})
    ep = client.get("/dashboard").json()["expiring_policies"]
    assert len(ep) == 1
    assert ep[0]["days"] == 10
    assert "insured_period" in ep[0] and "end_date_derived" in ep[0]
