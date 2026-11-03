"""실제 Supabase와 가까운 조건에서 migration 권한을 다시 검증한다(로컬 Postgres, 실서비스 아님).

Supabase 특성 흉내:
- public 스키마 기본 권한: 새 테이블/함수/시퀀스에 anon·authenticated·service_role 전체 권한 자동 부여(ALTER DEFAULT PRIVILEGES)
- auth.users 소유자는 supabase_auth_admin이고, 이메일 확인 UPDATE는 그 역할로 실행됨
- SQL Editor 적용 역할 = postgres(여기서는 로컬 슈퍼유저)
"""
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_entitlements_pg import MIGRATION, ROLLBACK, SHIM, As, server  # noqa: F401 (fixture)

psycopg = pytest.importorskip("psycopg")
ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "supabase" / "live"
DENIED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.InvalidAuthorizationSpecification)

SUPABASE_LIKE = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'supabase_auth_admin') then create role supabase_auth_admin nologin; end if;
end $$;
alter table auth.users owner to supabase_auth_admin;
grant usage on schema auth to supabase_auth_admin;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
"""


@pytest.fixture
def sdb(server):
    name = "s_" + uuid.uuid4().hex[:12]
    admin_uri = server.get_uri()
    with psycopg.connect(admin_uri, autocommit=True) as c:
        c.execute(f"create database {name}")
    uri = admin_uri.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute(SHIM.read_text(encoding="utf-8"))
        c.execute(SUPABASE_LIKE)
        # 기존 Auth 계정(과거에 이메일 확인 완료, 미확인 1명) — migration 전 존재
        c.execute("insert into auth.users (id, email, created_at, email_confirmed_at) values (%s, 'old@example.test', now() - interval '40 days', now() - interval '40 days')", (uuid.uuid4(),))
        c.execute("insert into auth.users (id, email, created_at, email_confirmed_at) values (%s, 'recent@example.test', now() - interval '2 days', now() - interval '2 days')", (uuid.uuid4(),))
        c.execute("insert into auth.users (id, email) values (%s, 'pending@example.test')", (uuid.uuid4(),))
    yield uri
    with psycopg.connect(admin_uri, autocommit=True) as c:
        c.execute(f"drop database {name} with (force)")


def q(uri, sql, params=None):
    with psycopg.connect(uri, autocommit=True) as c:
        cur = c.execute(sql, params) if params else c.execute(sql)
        return cur.fetchall() if cur.description else None


def uid_of(uri, email):
    return q(uri, "select id from auth.users where email = %s", (email,))[0][0]


def test_precheck_apply_postcheck_with_supabase_default_privileges(sdb):
    pre = dict(q(sdb, (LIVE / "00_precheck_readonly.sql").read_text(encoding="utf-8")))
    assert pre["moadoom_objects_existing"] == "0" and pre["auth_users_total"] == "3"
    assert pre["auth_users_confirmed"] == "2" and pre["auth_users_triggers_moadoom"] == "0"
    before = q(sdb, "select id, email, created_at, email_confirmed_at from auth.users order by email")
    q(sdb, MIGRATION.read_text(encoding="utf-8"))
    # 기존 Auth 행은 변경 없음, migration만으로 체험 지급 없음(트리거는 이후 확인 이벤트에만)
    assert q(sdb, "select id, email, created_at, email_confirmed_at from auth.users order by email") == before
    assert q(sdb, "select count(*) from public.moadoom_subscriptions")[0][0] == 0
    post = q(sdb, (LIVE / "30_postcheck_readonly.sql").read_text(encoding="utf-8"))
    bad = [r for r in post if r[2] != "OK"]
    assert not bad, bad


def test_default_privileges_do_not_open_tables_or_internal_functions(sdb):
    q(sdb, MIGRATION.read_text(encoding="utf-8"))
    member = As(sdb, uid_of(sdb, "recent@example.test"))
    anon = As(sdb, None)
    for who in (member, anon):
        for sql in [
            "insert into public.moadoom_member_roles (user_id, role, granted_by) values (gen_random_uuid(), 'owner', 'x')",
            "update public.moadoom_subscriptions set kind = 'lifetime', ends_at = null",
            "delete from public.moadoom_trial_grants",
            "select * from public.moadoom_email_outbox",
            "select * from public.moadoom_subscription_audit",
            "select * from public.moadoom_settings",
            "select public.moadoom_ensure_signup_trial(gen_random_uuid())",
            "select public.moadoom_enqueue_email(gen_random_uuid(), 'welcome_trial', 'x', '{}')",
            "select public.moadoom_require_role(array['owner'])",
            "select public.moadoom_role_of(gen_random_uuid())",
            "select public.moadoom_subscription_state(gen_random_uuid())",
            "select public.moadoom_setting('trial_days')",
            "select public.moadoom_kst(now())",
            "select public.moadoom_mail_claim(1)",
            "select public.moadoom_enqueue_expiry_notices()",
        ]:
            with pytest.raises(DENIED):
                who.run(sql)


def test_existing_confirmed_users_get_retroactive_trial_on_first_lookup(sdb):
    q(sdb, MIGRATION.read_text(encoding="utf-8"))
    old, recent = uid_of(sdb, "old@example.test"), uid_of(sdb, "recent@example.test")
    s_old = As(sdb, old).one("select public.moadoom_my_entitlement()")["subscription"]
    s_recent = As(sdb, recent).one("select public.moadoom_my_entitlement()")["subscription"]
    # 소급: 확인 시각부터 7일 → 40일 전 확인은 이미 만료, 2일 전 확인은 5일 남음
    assert s_old["status"] == "expired" and s_old["kind"] == "trial"
    assert s_recent["status"] == "active" and s_recent["days_left"] == 5
    # 미확인 계정이 이후 확인(supabase_auth_admin 역할로 UPDATE) → 트리거로 지금부터 7일
    pending = uid_of(sdb, "pending@example.test")
    with psycopg.connect(sdb) as c:
        with c.transaction():
            c.execute("set local role supabase_auth_admin")
            c.execute("update auth.users set email_confirmed_at = now() where id = %s", (pending,))
    s = q(sdb, "select public.moadoom_subscription_state(%s)", (pending,))[0][0]
    assert s["kind"] == "trial" and s["status"] == "active" and s["days_left"] == 7


def test_owner_seed_file_is_uuid_only_and_requires_confirmed_user(sdb):
    q(sdb, MIGRATION.read_text(encoding="utf-8"))
    seed = (ROOT / "supabase" / "seed_owner.sql").read_text(encoding="utf-8")
    assert "00000000-0000-0000-0000-000000000000" in seed and "email_confirmed_at is not null" in seed
    pending = uid_of(sdb, "pending@example.test")
    assert q(sdb, seed.replace("00000000-0000-0000-0000-000000000000", str(pending))) == []  # 미확인 거부
    old = uid_of(sdb, "old@example.test")
    rows = q(sdb, seed.replace("00000000-0000-0000-0000-000000000000", str(old)))
    assert len(rows) == 1 and rows[0][1] == "owner"
    # 이전에 만료된 체험이 있어도 소유자는 사용 가능, 관리 RPC 사용 가능
    e = As(sdb, old).one("select public.moadoom_my_entitlement()")
    assert e["role"] == "owner" and e["effective_status"] == "active"
    assert As(sdb, old).run("select count(*) from public.moadoom_admin_search_members('')")[0][0] == 3
    # 기존 회원에게 새 7일을 주는 경로 = 소유자 '무료체험 지정'(감사 기록)
    recent = uid_of(sdb, "recent@example.test")
    st = As(sdb, old).one("select public.moadoom_admin_change_subscription(%s, 'trial', null, 7, '기존 회원 체험 재지정')", (recent,))
    assert st["kind"] == "trial" and st["days_left"] == 7


def test_rollback_leaves_existing_auth_rows(sdb):
    before = q(sdb, "select id, email, created_at, email_confirmed_at from auth.users order by email")
    q(sdb, MIGRATION.read_text(encoding="utf-8"))
    q(sdb, ROLLBACK.read_text(encoding="utf-8"))
    assert q(sdb, "select id, email, created_at, email_confirmed_at from auth.users order by email") == before
    pre = dict(q(sdb, (LIVE / "00_precheck_readonly.sql").read_text(encoding="utf-8")))
    assert pre["moadoom_objects_existing"] == "0" and pre["auth_users_triggers_moadoom"] == "0"


def test_keygen_env_writes_private_key_only_to_file(tmp_path):
    env = tmp_path / "secrets.env"
    r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(ROOT / "supabase" / "tools" / "license_cli.ts")],
                       input=json.dumps({"op": "keygen_env", "env_out": str(env), "kid": "moadoom-test"}),
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out) == {"kid", "public_key"} and out["kid"] == "moadoom-test"
    text = env.read_text(encoding="utf-8")
    key = text.split("MOADOOM_LICENSE_SIGNING_KEY=", 1)[1].split("\n", 1)[0]
    assert len(key) > 40 and key not in r.stdout and key not in r.stderr
    assert "MOADOOM_LICENSE_KID=moadoom-test" in text
    # 기존 파일 덮어쓰기 거부
    r2 = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(ROOT / "supabase" / "tools" / "license_cli.ts")],
                        input=json.dumps({"op": "keygen_env", "env_out": str(env), "kid": "moadoom-test"}),
                        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    assert r2.returncode != 0
