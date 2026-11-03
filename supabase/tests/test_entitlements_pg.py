"""중앙 이용권 migration을 실제 Postgres(로컬 pgserver)에서 검증한다.

주의: Supabase Auth/PostgREST 자체가 아니라 auth.uid()/역할을 흉내 낸 로컬 Postgres다.
실서비스 검증으로 보고하지 않는다.
"""
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pgserver = pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261103000000_moadoom_central_entitlements.sql"
ROLLBACK = ROOT / "rollback" / "20261103000000_moadoom_central_entitlements.down.sql"
SHIM = Path(__file__).with_name("supabase_shim.sql")
PGDATA = Path(os.environ.get("MOADOOM_TEST_PGDATA", "C:/build/central-pg-data-tests"))
KST = timezone(timedelta(hours=9))
DENIED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.InvalidAuthorizationSpecification)


@pytest.fixture(scope="session")
def server():
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    if not (PGDATA / "PG_VERSION").exists() and sys.flags.utf8_mode:
        # Windows 한글 사용자명: initdb 출력이 cp949라 pgserver(UTF-8 디코딩)가 새 데이터 폴더 초기화에서 죽는다.
        pytest.fail("새 PGDATA 초기화는 PYTHONUTF8=0 으로 실행해야 합니다(Windows 한글 사용자명 initdb 출력 디코딩).")
    srv = pgserver.get_server(str(PGDATA).replace("\\", "/"), cleanup_mode="stop")
    yield srv
    try:
        srv.cleanup()
    except Exception:
        pass


@pytest.fixture
def db(server):
    name = "t_" + uuid.uuid4().hex[:12]
    admin_uri = server.get_uri()
    with psycopg.connect(admin_uri, autocommit=True) as c:
        c.execute(f"create database {name}")
    uri = admin_uri.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute(SHIM.read_text(encoding="utf-8"))
        c.execute(MIGRATION.read_text(encoding="utf-8"))
    yield uri
    with psycopg.connect(admin_uri, autocommit=True) as c:
        c.execute(f"drop database {name} with (force)")


def sql(uri, q, params=()):
    """프로젝트 소유자(postgres) 권한 — 대시보드 SQL / Auth 서버 동작 흉내."""
    with psycopg.connect(uri, autocommit=True) as c:
        cur = c.execute(q, params)
        return cur.fetchall() if cur.description else None


def add_user(uri, email, confirmed=True, confirmed_at=None):
    uid = uuid.uuid4()
    sql(uri, "insert into auth.users (id, email, email_confirmed_at) values (%s, %s, %s)",
        (uid, email, confirmed_at if confirmed_at is not None else ("now()" if confirmed else None)))
    return uid


def confirm(uri, uid, at="now()"):
    sql(uri, "update auth.users set email_confirmed_at = %s where id = %s", (at, uid))


def make_owner(uri, uid, role="owner"):
    sql(uri, "insert into public.moadoom_member_roles (user_id, role, granted_by) values (%s, %s, 'dashboard-sql')",
        (uid, role))


class As:
    """한 요청 = 한 트랜잭션. role/claims는 트랜잭션 로컬(PostgREST와 같은 방식)."""

    def __init__(self, uri, uid=None, role=None):
        self.uri, self.uid = uri, uid
        self.role = role or ("authenticated" if uid else "anon")

    def run(self, q, params=()):
        with psycopg.connect(self.uri) as c:
            with c.transaction():
                c.execute("set local role " + self.role)
                claims = json.dumps({"sub": str(self.uid), "role": self.role}) if self.uid else json.dumps({"role": self.role})
                c.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
                cur = c.execute(q, params)
                return cur.fetchall() if cur.description else None

    def one(self, q, params=()):
        return self.run(q, params)[0][0]


def change(actor, target, action, ends_on=None, days=None, reason="테스트"):
    return actor.one("select public.moadoom_admin_change_subscription(%s, %s, %s, %s, %s)",
                     (target, action, ends_on, days, reason))


def ent(uri, uid):
    return As(uri, uid).one("select public.moadoom_my_entitlement()")


def ts(v):
    return datetime.fromisoformat(v)


def outbox(uri, uid):
    return sql(uri, "select kind, dedupe_key, status from public.moadoom_email_outbox where user_id = %s order by id", (uid,))


# ------------------------------------------------------------------ 무료체험 7일
def test_email_confirmation_starts_exact_seven_day_trial_once(db):
    uid = add_user(db, "new@example.test", confirmed=False)
    e = ent(db, uid)
    assert e["subscription"]["status"] == "none" and e["effective_status"] == "none"  # 확인 전 체험 없음
    confirm(db, uid)
    confirmed_at = sql(db, "select email_confirmed_at from auth.users where id = %s", (uid,))[0][0]
    s = ent(db, uid)["subscription"]
    assert s["kind"] == "trial" and s["status"] == "active" and s["plan"] == "TRIAL"
    assert ts(s["starts_at"]) == confirmed_at and ts(s["trial_started_at"]) == confirmed_at
    assert ts(s["ends_at"]) - confirmed_at == timedelta(days=7)  # 이메일 확인 시각 + 정확히 7일
    assert s["days_left"] == 7 and 0 < s["remaining_seconds"] <= 7 * 86400
    audit = sql(db, "select action, actor_user_id, reason from public.moadoom_subscription_audit where target_user_id = %s", (uid,))
    assert len(audit) == 1 and audit[0][0] == "trial_auto" and audit[0][1] is None and "이메일 확인 완료" in audit[0][2]
    assert outbox(db, uid) == [("welcome_trial", f"welcome_trial:{uid}", "queued")]


def test_trial_is_not_reset_by_callback_replay_relogin_or_password_reset(db):
    uid = add_user(db, "once@example.test", confirmed=False)
    confirm(db, uid)
    first = ent(db, uid)["subscription"]
    # 확인 링크 재사용/중복 callback: 확인 시각이 다시 바뀌어도 새 체험 없음
    confirm(db, uid, datetime.now(timezone.utc) + timedelta(days=3))
    sql(db, "update auth.users set email_confirmed_at = null where id = %s", (uid,))
    confirm(db, uid)
    for _ in range(3):  # 재로그인/앱 재설치 = 이용권 재조회
        again = ent(db, uid)["subscription"]
    assert (again["starts_at"], again["ends_at"]) == (first["starts_at"], first["ends_at"])
    assert sql(db, "select count(*) from public.moadoom_trial_grants")[0][0] == 1
    assert sql(db, "select count(*) from public.moadoom_subscription_audit where action = 'trial_auto'")[0][0] == 1
    assert len(outbox(db, uid)) == 1


def test_deleted_and_recreated_account_with_same_email_gets_no_second_trial(db):
    uid = add_user(db, "Again@Example.test")
    assert ent(db, uid)["subscription"]["kind"] == "trial"
    sql(db, "delete from auth.users where id = %s", (uid,))
    uid2 = add_user(db, "again@example.TEST ")  # 대소문자/공백 달라도 같은 이메일
    e = ent(db, uid2)
    assert e["subscription"]["status"] == "none" and e["effective_status"] == "none"
    assert sql(db, "select count(*) from public.moadoom_trial_grants")[0][0] == 1


def test_trial_boundary_uses_exact_server_time(db):
    almost = add_user(db, "almost@example.test", confirmed_at=datetime.now(timezone.utc) - timedelta(days=7) + timedelta(seconds=60))
    over = add_user(db, "over@example.test", confirmed_at=datetime.now(timezone.utc) - timedelta(days=7) - timedelta(seconds=1))
    a = ent(db, almost)["subscription"]
    o = ent(db, over)["subscription"]
    assert a["status"] == "active" and 0 < a["remaining_seconds"] <= 60 and a["days_left"] == 1
    assert o["status"] == "expired" and o["remaining_seconds"] == 0 and o["days_left"] == 0
    # 날짜(한국 자정)가 아니라 확인 시각 기준
    old_kst_evening = datetime(2026, 1, 1, 23, 30, tzinfo=KST)
    late = add_user(db, "late@example.test", confirmed_at=old_kst_evening)
    s = ent(db, late)["subscription"]
    assert ts(s["ends_at"]) == datetime(2026, 1, 8, 23, 30, tzinfo=KST)


def test_unconfirmed_user_cannot_get_trial_and_self_heal_after_missed_trigger(db):
    uid = add_user(db, "heal@example.test", confirmed=False)
    with psycopg.connect(db, autocommit=True) as c:  # 트리거가 없던 시점의 확인(예: migration 이전 확인 회원)
        c.execute("alter table auth.users disable trigger moadoom_auth_user_confirmed")
        c.execute("update auth.users set email_confirmed_at = now() - interval '2 days' where id = %s", (uid,))
        c.execute("alter table auth.users enable trigger moadoom_auth_user_confirmed")
    s = ent(db, uid)["subscription"]  # 첫 조회 때 확인 시각 기준으로 지급(5일 남음)
    assert s["kind"] == "trial" and s["status"] == "active" and s["days_left"] == 5


# ------------------------------------------------------------------ 권한/RLS/IDOR
def test_members_cannot_write_tables_or_self_grant(db):
    uid = add_user(db, "member@example.test")
    me = As(db, uid)
    for q in [
        "insert into public.moadoom_subscriptions (user_id, kind, starts_at) values (auth.uid(), 'lifetime', now())",
        "update public.moadoom_subscriptions set kind = 'lifetime', ends_at = null",
        "insert into public.moadoom_member_roles (user_id, role, granted_by) values (auth.uid(), 'owner', 'self')",
        "insert into public.moadoom_trial_grants (user_id, email_hash, starts_at, ends_at) values (auth.uid(), 'x', now(), now() + interval '1 day')",
        "delete from public.moadoom_trial_grants",
        "update public.moadoom_settings set value = '30' where key = 'trial_days'",
        "select * from public.moadoom_email_outbox",
        "select * from public.moadoom_subscription_audit",
        "select public.moadoom_ensure_signup_trial(auth.uid())",
        "select public.moadoom_mail_claim(10)",
        "select public.moadoom_enqueue_expiry_notices()",
    ]:
        with pytest.raises(DENIED):
            me.run(q)
    for action, kw in [("lifetime", {}), ("trial", {"days": 30}), ("term", {"ends_on": date.today() + timedelta(days=365)}),
                       ("adjust", {"days": 100}), ("resume", {})]:
        with pytest.raises(DENIED):
            change(me, uid, action, **kw)


def test_anonymous_is_rejected_everywhere(db):
    target = add_user(db, "member@example.test")
    anon = As(db, None)
    for q, params in [
        ("select public.moadoom_my_entitlement()", ()),
        ("select * from public.moadoom_admin_search_members('')", ()),
        ("select public.moadoom_admin_member_detail(%s)", (target,)),
        ("select public.moadoom_admin_change_subscription(%s, 'lifetime', null, null, 'x')", (target,)),
        ("select public.moadoom_admin_settings()", ()),
        ("select * from public.moadoom_subscriptions", ()),
    ]:
        with pytest.raises(DENIED):
            anon.run(q, params)


def test_regular_member_cannot_read_others_idor(db):
    a = add_user(db, "a@example.test")
    b = add_user(db, "b@example.test")
    member = As(db, a)
    ent(db, a), ent(db, b)
    for q, params in [("select * from public.moadoom_admin_search_members('')", ()),
                      ("select public.moadoom_admin_member_detail(%s)", (b,))]:
        with pytest.raises(DENIED):
            member.run(q, params)
    assert member.run("select user_id from public.moadoom_subscriptions") == [(a,)]  # 본인 행만
    assert member.run("select count(*) from public.moadoom_member_roles")[0][0] == 0


def test_admin_role_is_read_only_under_current_policy(db):
    admin = add_user(db, "admin@example.test")
    make_owner(db, admin, "admin")
    m = add_user(db, "m@example.test")
    assert As(db, admin).run("select count(*) from public.moadoom_admin_search_members('')")[0][0] == 2
    assert As(db, admin).one("select public.moadoom_admin_member_detail(%s)", (m,))["email"] == "m@example.test"
    with pytest.raises(DENIED):
        change(As(db, admin), m, "lifetime")
    with pytest.raises(DENIED):
        As(db, admin).run("select public.moadoom_admin_set_expiry_notice_days(array[7])")


# ------------------------------------------------------------------ 소유자 지정 무기한 무료
def test_owner_grants_and_removes_lifetime_without_granting_roles(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    m = add_user(db, "vip@example.test")
    op = As(db, owner)
    # 소유자는 이용권과 무관하게 active(운영 권한), 자기 이용권은 못 바꿈
    assert ent(db, owner)["effective_status"] == "active"
    with pytest.raises(DENIED):
        change(op, owner, "lifetime")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "lifetime", reason="  ")
    st = change(op, m, "lifetime", reason="지인 무기한 무료")
    assert st["kind"] == "lifetime" and st["ends_at"] is None and st["status"] == "active"
    assert st["days_left"] is None and st["remaining_seconds"] is None
    row = sql(db, "select kind, ends_at from public.moadoom_subscriptions where user_id = %s", (m,))[0]
    assert row == ("lifetime", None)  # 2099 같은 가짜 날짜 없음
    e = ent(db, m)
    assert e["effective_status"] == "active" and e["role"] is None  # 역할 아님
    with pytest.raises(DENIED):  # 무기한 회원도 관리 기능 없음
        As(db, m).run("select * from public.moadoom_admin_search_members('')")
    with pytest.raises(DENIED):
        change(As(db, m), owner, "suspend")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "adjust", days=10)
    # 무기한 해제 = 기간지정(또는 정지)
    end = date.today() + timedelta(days=2)
    st = change(op, m, "term", ends_on=end, reason="무기한 해제, 2일 뒤까지")
    assert st["kind"] == "term" and ts(st["ends_at"]) == datetime(end.year, end.month, end.day, tzinfo=KST) + timedelta(days=1)
    detail = op.one("select public.moadoom_admin_member_detail(%s)", (m,))
    assert [a["action"] for a in detail["audit"]][:3] == ["term", "lifetime", "trial_auto"]
    assert detail["audit"][1]["actor_email"] == "owner@example.test" and detail["audit"][2]["actor_email"] == "시스템"
    assert [x["kind"] for x in detail["mail"]].count("entitlement_changed") == 2


def test_owner_trial_term_adjust_suspend_resume(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    m = add_user(db, "m@example.test")
    op = As(db, owner)
    trial_end = ts(ent(db, m)["subscription"]["ends_at"])
    st = change(op, m, "adjust", days=3, reason="체험 3일 연장")
    assert ts(st["ends_at"]) == trial_end + timedelta(days=3) and st["kind"] == "trial"
    st = change(op, m, "adjust", days=-2, reason="2일 단축")
    assert ts(st["ends_at"]) == trial_end + timedelta(days=1)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "adjust", days=-30, reason="시작보다 앞")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "adjust", days=0)
    st = change(op, m, "suspend", reason="이용정지")
    assert st["status"] == "suspended"
    assert ent(db, m)["effective_status"] == "suspended"
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "suspend")
    st = change(op, m, "resume", reason="정지 해제")
    assert st["status"] == "active" and ts(st["ends_at"]) == trial_end + timedelta(days=1)  # 기간 유지
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "term", ends_on=date.today() - timedelta(days=1))
    st = change(op, m, "trial", days=14, reason="체험 재지정")
    assert st["kind"] == "trial" and 13 * 86400 < st["remaining_seconds"] <= 14 * 86400
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        change(op, m, "delete")
    with pytest.raises(psycopg.errors.NoDataFound):
        change(op, uuid.uuid4(), "lifetime")


def test_owner_choice_before_confirmation_is_not_overwritten_by_trial(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    m = add_user(db, "pre@example.test", confirmed=False)
    change(As(db, owner), m, "lifetime", reason="가입 직후 무기한 지정")
    confirm(db, m)
    s = ent(db, m)["subscription"]
    assert s["kind"] == "lifetime" and s["trial_started_at"] is not None  # 체험은 기록만(재가입 체험 방지)


def test_expired_extension_starts_from_now(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    m = add_user(db, "old@example.test", confirmed_at=datetime.now(timezone.utc) - timedelta(days=20))
    assert ent(db, m)["subscription"]["status"] == "expired"
    st = change(As(db, owner), m, "adjust", days=10, reason="만료 후 연장")
    assert st["status"] == "active" and 9 * 86400 < st["remaining_seconds"] <= 10 * 86400


def test_search_escapes_wildcards_and_reports_confirmation(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    add_user(db, "a_b@example.test")
    add_user(db, "axb@example.test", confirmed=False)
    rows = As(db, owner).run("select email, email_confirmed from public.moadoom_admin_search_members('a_b')")
    assert rows == [("a_b@example.test", True)]
    rows = As(db, owner).run("select email, email_confirmed, subscription->>'status' from public.moadoom_admin_search_members('axb')")
    assert rows == [("axb@example.test", False, "none")]


def test_role_removal_is_immediate(db):
    admin = add_user(db, "admin@example.test")
    make_owner(db, admin, "admin")
    assert As(db, admin).run("select count(*) from public.moadoom_admin_search_members('')")[0][0] == 1
    sql(db, "delete from public.moadoom_member_roles where user_id = %s", (admin,))
    with pytest.raises(DENIED):
        As(db, admin).run("select * from public.moadoom_admin_search_members('')")


# ------------------------------------------------------------------ 안내 메일 발송함
def test_expiry_notices_use_configurable_days_and_never_duplicate(db):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    svc = As(db, role="service_role")
    m = add_user(db, "soon@example.test", confirmed_at=datetime.now(timezone.utc) - timedelta(days=4, hours=12))  # 2.5일 남음
    ent(db, m)
    assert As(db, owner).one("select public.moadoom_admin_settings()")["expiry_notice_days"] == [3, 1]
    assert svc.one("select public.moadoom_enqueue_expiry_notices()") == 1
    assert svc.one("select public.moadoom_enqueue_expiry_notices()") == 0  # 중복 없음
    change(As(db, owner), m, "adjust", days=-2, reason="0.5일 남게")
    assert svc.one("select public.moadoom_enqueue_expiry_notices()") == 1  # 1일 전 안내
    kinds = [k for k, _, _ in outbox(db, m)]
    assert kinds.count("expiry_notice") == 2 and kinds.count("welcome_trial") == 1
    days_before = sorted(r[0] for r in sql(db, "select (payload->>'days_before')::int from public.moadoom_email_outbox where kind='expiry_notice'"))
    assert days_before == [1, 3]
    # 설정 변경(소유자만) + 값 검증
    assert As(db, owner).one("select public.moadoom_admin_set_expiry_notice_days(array[7, 1, 7])") == [7, 1]
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        As(db, owner).run("select public.moadoom_admin_set_expiry_notice_days(array[0])")
    # lifetime/정지 회원은 만료 안내 없음
    v = add_user(db, "vip@example.test", confirmed_at=datetime.now(timezone.utc) - timedelta(days=6, hours=12))
    change(As(db, owner), v, "lifetime", reason="무기한")
    assert svc.one("select public.moadoom_enqueue_expiry_notices()") == 0


def test_mail_claim_retry_and_success_require_provider_id(db):
    m = add_user(db, "mail@example.test")
    ent(db, m)
    svc = As(db, role="service_role")
    claimed = svc.run("select id, kind, status, attempts from public.moadoom_mail_claim(10)")
    assert len(claimed) == 1 and claimed[0][1:] == ("welcome_trial", "sending", 1)
    mid = claimed[0][0]
    assert svc.run("select * from public.moadoom_mail_claim(10)") == []  # 이미 처리 중(중복 발송 없음)
    assert svc.one("select public.moadoom_mail_result(%s, true, null, null)", (mid,)) == "queued"  # 공급자 ID 없으면 성공 아님
    sql(db, "update public.moadoom_email_outbox set next_attempt_at = now() where id = %s", (mid,))
    svc.run("select * from public.moadoom_mail_claim(10)")
    assert svc.one("select public.moadoom_mail_result(%s, false, 'smtp 451', null)", (mid,)) == "queued"
    row = sql(db, "select attempts, last_error, next_attempt_at > now() + interval '3 minutes' from public.moadoom_email_outbox where id = %s", (mid,))[0]
    assert row == (2, "smtp 451", True)  # 지수 백오프(2^2분)
    sql(db, "update public.moadoom_email_outbox set next_attempt_at = now() where id = %s", (mid,))
    svc.run("select * from public.moadoom_mail_claim(10)")
    assert svc.one("select public.moadoom_mail_result(%s, true, null, 'prov-123')", (mid,)) == "sent"
    assert sql(db, "select status, provider_message_id from public.moadoom_email_outbox where id = %s", (mid,))[0] == ("sent", "prov-123")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        svc.run("select public.moadoom_mail_result(%s, true, null, 'again')", (mid,))
    # 최대 시도 초과 → failed
    sql(db, "update public.moadoom_email_outbox set status = 'queued', attempts = 4, next_attempt_at = now() where id = %s", (mid,))
    svc.run("select * from public.moadoom_mail_claim(10)")
    assert svc.one("select public.moadoom_mail_result(%s, false, 'down', null)", (mid,)) == "failed"


def test_migration_is_idempotent_and_rollback_leaves_users(db):
    uid = add_user(db, "keep@example.test")
    ent(db, uid)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute(MIGRATION.read_text(encoding="utf-8"))  # 재실행 안전(기존 행 유지)
        assert c.execute("select count(*) from public.moadoom_trial_grants").fetchone()[0] == 1
        c.execute(ROLLBACK.read_text(encoding="utf-8"))
        left = c.execute("select count(*) from pg_tables where tablename like 'moadoom_%'").fetchone()[0]
        users = c.execute("select count(*) from auth.users where id = %s", (uid,)).fetchone()[0]
        fns = c.execute("select count(*) from pg_proc where proname like 'moadoom_%'").fetchone()[0]
        trg = c.execute("select count(*) from pg_trigger where tgname like 'moadoom_%'").fetchone()[0]
    assert (left, fns, trg, users) == (0, 0, 0, 1)
