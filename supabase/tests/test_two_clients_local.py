"""로컬 E2E: 같은 회원으로 '별도 클라이언트 2개'가 이용권을 받아 검증하는 흐름.

실제 구성요소: Postgres 16(로컬 pgserver) + 실제 migration/RPC/트리거, 실제 Ed25519 서명(Edge Function과 같은 코드),
실제 클라이언트 검증 코드(desktop/src/licenseToken.ts). 대역: Supabase Auth/PostgREST/Edge 런타임(→ auth.uid() 흉내).
따라서 '로컬 통합 검증'이며 live 서비스 검증이 아니다.
"""
import base64
import json
import os
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from test_entitlements_pg import As, add_user, change, confirm, db, make_owner, server  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "supabase" / "tools" / "license_cli.ts"
NODE = os.environ.get("MOADOOM_NODE", "node")


def cli(payload):
    r = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(CLI)], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def keys(tmp_path):
    priv = tmp_path / "license-signing.pkcs8"  # 테스트 임시 파일 — 출력하지 않음
    pub = cli({"op": "keygen", "private_out": str(priv), "kid": "test-k1"})
    return {"kid": pub["kid"], "private_file": str(priv), "public_keys": {pub["kid"]: pub["public_key"]}}


class Client:
    """PC 한 대를 흉내: 자기 캐시만 가진다(다른 PC와 공유 없음)."""

    def __init__(self, name, db_uri, uid, email, keys):
        self.name, self.db, self.uid, self.email, self.keys = name, db_uri, uid, email, keys
        self.cache = None
        self.online = True

    def edge_issue(self):
        # Edge Function moadoom-license 와 동일: 호출자 JWT(=uid)로 본인 이용권 RPC → 서명
        ent = As(self.db, self.uid).one("select public.moadoom_my_entitlement()")
        return cli({"op": "sign", "ent": ent, "user": {"id": str(self.uid), "email": self.email},
                    "kid": self.keys["kid"], "private_file": self.keys["private_file"], "now": time.time()})["license"]

    def refresh(self, now=None):
        now = now or time.time()
        if self.online:
            lic = self.edge_issue()
            r = cli({"op": "verify", "license": lic, "public_keys": self.keys["public_keys"], "sub": str(self.uid), "now": now})
            assert r["ok"], r
            self.cache = lic
            return {"source": "online", "effective": r["effective"], "kind": r["payload"]["kind"], "ends_at": r["payload"]["ends_at"]}
        if not self.cache:
            return {"source": "offline", "effective": None, "error": "server_unreachable"}
        r = cli({"op": "verify", "license": self.cache, "public_keys": self.keys["public_keys"], "sub": str(self.uid),
                 "now": now, "offline": True})
        if not r["ok"]:
            return {"source": "offline", "effective": None, "error": r["reason"]}
        return {"source": "offline", "effective": r["effective"], "kind": r["payload"]["kind"], "ends_at": r["payload"]["ends_at"]}


def test_two_clients_same_member_trial_lifetime_term_adjust_suspend(db, keys):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    member = add_user(db, "member@example.test", confirmed=False)
    a = Client("PC-A", db, member, "member@example.test", keys)
    b = Client("PC-B", db, member, "member@example.test", keys)
    op = As(db, owner)

    assert [c.refresh()["effective"] for c in (a, b)] == ["none", "none"]  # 이메일 확인 전
    confirm(db, member)  # 사이트 확인 링크
    ra, rb = a.refresh(), b.refresh()
    assert ra["effective"] == rb["effective"] == "active" and ra["kind"] == rb["kind"] == "trial"
    assert ra["ends_at"] == rb["ends_at"]

    change(op, member, "lifetime", reason="무기한 지정")
    ra, rb = a.refresh(), b.refresh()
    assert (ra["kind"], ra["ends_at"], rb["kind"], rb["ends_at"]) == ("lifetime", None, "lifetime", None)

    # 무기한도 오프라인 5일 초과면 서버 확인 필요(만료가 아니라 stale)
    b.online = False
    assert b.refresh()["effective"] == "active"
    assert b.refresh(now=time.time() + 6 * 86400)["error"] == "stale"
    b.online = True

    end = date.today() + timedelta(days=30)
    change(op, member, "term", ends_on=end, reason="무기한 해제 → 30일")
    ra, rb = a.refresh(), b.refresh()
    assert ra["kind"] == rb["kind"] == "term" and ra["ends_at"] == rb["ends_at"] is not None
    change(op, member, "adjust", days=10, reason="연장")
    assert a.refresh()["ends_at"] == b.refresh()["ends_at"] != ra["ends_at"]

    change(op, member, "suspend", reason="정지")
    assert a.refresh()["effective"] == b.refresh()["effective"] == "suspended"
    change(op, member, "resume", reason="해제")
    assert a.refresh()["effective"] == b.refresh()["effective"] == "active"


def test_owner_is_active_on_both_clients_without_subscription(db, keys):
    owner = add_user(db, "owner@example.test")
    make_owner(db, owner)
    a = Client("PC-A", db, owner, "owner@example.test", keys)
    b = Client("PC-B", db, owner, "owner@example.test", keys)
    assert a.refresh()["effective"] == b.refresh()["effective"] == "active"


def test_client_cannot_forge_or_borrow_a_license(db, keys):
    m1 = add_user(db, "m1@example.test")
    m2 = add_user(db, "m2@example.test")
    a = Client("PC-A", db, m1, "m1@example.test", keys)
    a.refresh()
    r = cli({"op": "verify", "license": a.cache, "public_keys": keys["public_keys"], "sub": str(m2), "now": time.time(), "offline": True})
    assert r == {"ok": False, "reason": "wrong_user"}
    p = json.loads(base64.urlsafe_b64decode(a.cache["payload"] + "=="))
    p.update(kind="lifetime", ends_at=None)
    forged = dict(a.cache, payload=base64.urlsafe_b64encode(json.dumps(p).encode()).decode().rstrip("="))
    r = cli({"op": "verify", "license": forged, "public_keys": keys["public_keys"], "sub": str(m1), "now": time.time(), "offline": True})
    assert r == {"ok": False, "reason": "bad_signature"}
