"""로컬 QA 전용 Supabase 대역(emulator). 실제 Supabase가 아니다 — live 검증으로 보고 금지.

실제인 것: Postgres 16 + 실제 migration/RPC/RLS, 실제 Ed25519 서명 코드(license_cli → _shared/license.ts),
실제 사이트 HTML/JS(supabase-js 2.118 UMD)와 클라이언트 코드.
대역인 것: GoTrue(Auth) 엔드포인트, PostgREST rpc, Edge 런타임, 메일 발송(outbox로 대체).

실행: python qa_emulator.py --port 54399 --site C:/build/moadoom-central-site --keys <dir>
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import jwt
import pgserver
import psycopg
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20261103000000_moadoom_central_entitlements.sql"
SEED_OWNER = ROOT / "supabase" / "seed_owner.sql"
SHIM = HERE / "supabase_shim.sql"
CLI = ROOT / "supabase" / "tools" / "license_cli.ts"
PUBLISHABLE = "sb_publishable_qa_local_only"
JWT_SECRET = secrets.token_hex(32)
RPC = {
    "moadoom_my_entitlement": ("scalar", []),
    "moadoom_admin_member_detail": ("scalar", ["target"]),
    "moadoom_admin_change_subscription": ("scalar", ["target", "action", "ends_on", "days", "reason"]),
    "moadoom_admin_search_members": ("set", ["q"]),
    "moadoom_admin_settings": ("scalar", []),
    "moadoom_admin_set_expiry_notice_days": ("scalar", ["days_list"]),
}
ERR = {"42501": 403, "28000": 401, "P0002": 404, "22023": 400, "23514": 400}

args = None
DB = None
OUTBOX: list[dict] = []
PASSWORDS: dict[str, str] = {}  # QA 합성 계정만(메모리)
REFRESH: dict[str, str] = {}
STATE = {"license_down": False}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db():
    return psycopg.connect(DB, autocommit=True)


def user_row(uid=None, email=None):
    with db() as c:
        if uid:
            r = c.execute("select id, email, created_at, email_confirmed_at from auth.users where id = %s", (uid,)).fetchone()
        else:
            r = c.execute("select id, email, created_at, email_confirmed_at from auth.users where email = %s", (email,)).fetchone()
    if not r:
        return None
    return {
        "id": str(r[0]), "aud": "authenticated", "role": "authenticated", "email": r[1],
        "created_at": r[2].isoformat(), "email_confirmed_at": r[3].isoformat() if r[3] else None,
        "confirmed_at": r[3].isoformat() if r[3] else None,
        "app_metadata": {"provider": "email", "providers": ["email"]}, "user_metadata": {}, "identities": [],
        "updated_at": now_iso(),
    }


def session_for(user):
    exp = int(time.time()) + 3600
    token = jwt.encode({"sub": user["id"], "email": user["email"], "role": "authenticated", "aud": "authenticated",
                        "exp": exp, "iat": int(time.time()), "session_id": uuid.uuid4().hex}, JWT_SECRET, algorithm="HS256")
    refresh = secrets.token_hex(16)
    REFRESH[refresh] = user["id"]
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600, "expires_at": exp,
            "refresh_token": refresh, "user": user}


def bearer_user(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        claims = jwt.decode(auth[7:], JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError:
        return None
    return user_row(uid=claims["sub"])


def auth_error(status, code, msg):
    return JSONResponse({"code": status, "error_code": code, "msg": msg}, status_code=status)


@app.post("/auth/v1/signup")
async def signup(request: Request):
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if len(password) < 6:
        return auth_error(422, "weak_password", "Password should be at least 6 characters.")
    if user_row(email=email):
        return auth_error(422, "user_already_exists", "User already registered")
    uid = str(uuid.uuid4())
    with db() as c:
        c.execute("insert into auth.users (id, email) values (%s, %s)", (uid, email))
    PASSWORDS[uid] = password
    token = secrets.token_urlsafe(16)
    redirect = request.query_params.get("redirect_to", "")
    OUTBOX.append({"to": email, "type": "signup", "token": token, "uid": uid, "redirect_to": redirect,
                   "link": f"/__qa/verify?{urlencode({'token': token, 'type': 'signup', 'redirect_to': redirect})}"})
    return user_row(uid=uid)


@app.get("/__qa/verify")
def verify(token: str, type: str, redirect_to: str = ""):
    item = next((m for m in OUTBOX if m["token"] == token and m["type"] == type and not m.get("used")), None)
    if not item:
        return PlainTextResponse("link expired", status_code=403)
    item["used"] = True
    with db() as c:
        c.execute("update auth.users set email_confirmed_at = coalesce(email_confirmed_at, now()) where id = %s", (item["uid"],))
    s = session_for(user_row(uid=item["uid"]))
    frag = urlencode({"access_token": s["access_token"], "expires_at": s["expires_at"], "expires_in": 3600,
                      "refresh_token": s["refresh_token"], "token_type": "bearer", "type": type})
    return RedirectResponse(f"{redirect_to or '/'}#{frag}", status_code=303)


@app.post("/auth/v1/token")
async def token(request: Request):
    grant = request.query_params.get("grant_type")
    body = await request.json()
    if grant == "password":
        u = user_row(email=str(body.get("email", "")).strip().lower())
        if not u or PASSWORDS.get(u["id"]) != body.get("password"):
            return auth_error(400, "invalid_credentials", "Invalid login credentials")
        if not u["email_confirmed_at"]:
            return auth_error(400, "email_not_confirmed", "Email not confirmed")
        return session_for(u)
    if grant == "refresh_token":
        uid = REFRESH.pop(str(body.get("refresh_token", "")), None)
        if not uid:
            return auth_error(400, "refresh_token_not_found", "Invalid Refresh Token")
        return session_for(user_row(uid=uid))
    return auth_error(400, "unsupported_grant_type", "unsupported")


@app.get("/auth/v1/user")
def get_user(request: Request):
    u = bearer_user(request)
    return u if u else auth_error(401, "bad_jwt", "invalid JWT")


@app.put("/auth/v1/user")
async def put_user(request: Request):
    u = bearer_user(request)
    if not u:
        return auth_error(401, "bad_jwt", "invalid JWT")
    body = await request.json()
    if "password" in body:
        if len(str(body["password"])) < 6:
            return auth_error(422, "weak_password", "Password should be at least 6 characters.")
        PASSWORDS[u["id"]] = str(body["password"])
    return user_row(uid=u["id"])


@app.post("/auth/v1/logout")
def logout():
    return Response(status_code=204)


@app.post("/auth/v1/recover")
async def recover(request: Request):
    body = await request.json()
    u = user_row(email=str(body.get("email", "")).strip().lower())
    if u:
        token = secrets.token_urlsafe(16)
        redirect = request.query_params.get("redirect_to", "")
        OUTBOX.append({"to": u["email"], "type": "recovery", "token": token, "uid": u["id"], "redirect_to": redirect,
                       "link": f"/__qa/verify?{urlencode({'token': token, 'type': 'recovery', 'redirect_to': redirect})}"})
    return {}


@app.post("/rest/v1/rpc/{fn}")
async def rpc(fn: str, request: Request):
    if fn not in RPC:
        return JSONResponse({"code": "PGRST202", "message": f"function {fn} not found"}, status_code=404)
    kind, params = RPC[fn]
    body = await request.json() if (await request.body()) else {}
    u = bearer_user(request)
    named = ", ".join(f"{p} => %s" for p in params if p in body)
    values = [body[p] for p in params if p in body]
    sql = f"select public.{fn}({named})" if kind == "scalar" else f"select to_jsonb(t) from public.{fn}({named}) t"
    try:
        with psycopg.connect(DB) as c:
            with c.transaction():
                c.execute("set local role " + ("authenticated" if u else "anon"))
                claims = json.dumps({"sub": u["id"], "role": "authenticated"}) if u else "{}"
                c.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
                rows = c.execute(sql, values).fetchall()
    except psycopg.Error as e:
        code = e.sqlstate or "XX000"
        return JSONResponse({"code": code, "message": e.diag.message_primary or str(e), "details": None, "hint": None},
                            status_code=ERR.get(code, 400))
    return rows[0][0] if kind == "scalar" else [r[0] for r in rows]


@app.post("/functions/v1/moadoom-license")
def license_fn(request: Request):
    if STATE["license_down"]:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    u = bearer_user(request)
    if not u:
        return JSONResponse({"error": "login_required"}, status_code=401)
    if not u["email_confirmed_at"]:
        return JSONResponse({"error": "email_not_confirmed"}, status_code=403)
    with psycopg.connect(DB) as c:
        with c.transaction():
            c.execute("set local role authenticated")
            c.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": u["id"]}),))
            ent = c.execute("select public.moadoom_my_entitlement()").fetchone()[0]
    keys = json.loads((Path(args.keys) / "public.json").read_text())
    r = subprocess.run([args.node, "--experimental-strip-types", "--no-warnings", str(CLI)],
                       input=json.dumps({"op": "sign", "ent": ent, "user": {"id": u["id"], "email": u["email"]},
                                         "kid": keys["kid"], "private_file": str(Path(args.keys) / "signing.pkcs8")}),
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    if r.returncode != 0:
        return JSONResponse({"error": "signing_failed"}, status_code=500)
    return JSONResponse(json.loads(r.stdout), headers={"Cache-Control": "no-store"})


# ---- QA 전용 보조 ----
@app.get("/__qa/outbox")
def outbox(to: str = ""):
    return [{k: v for k, v in m.items() if k != "uid"} for m in OUTBOX if not to or m["to"] == to]


@app.post("/__qa/seed-owner")
async def seed_owner(request: Request):
    """대시보드 SQL Editor(프로젝트 소유자) 실행을 흉내: seed_owner.sql 그대로, UUID만 치환."""
    body = await request.json()
    uid = str(uuid.UUID(str(body["user_id"])))
    sql = SEED_OWNER.read_text(encoding="utf-8").replace("00000000-0000-0000-0000-000000000000", uid)
    with db() as c:
        rows = c.execute(sql).fetchall()
    return {"rows": len(rows)}


@app.post("/__qa/license-down")
async def license_down(request: Request):
    STATE["license_down"] = bool((await request.json()).get("down"))
    return STATE


@app.post("/__qa/shift-time")
async def shift_time(request: Request):
    """QA 전용: 회원의 확인 시각·이용권 시각을 과거로 민다(서버 시계를 바꾸지 않고 경계 상황 재현)."""
    body = await request.json()
    uid = str(uuid.UUID(str(body["user_id"])))
    secs = int(body["seconds"])
    with db() as c:
        c.execute("update auth.users set email_confirmed_at = email_confirmed_at - make_interval(secs => %s) where id = %s", (secs, uid))
        c.execute("update public.moadoom_trial_grants set starts_at = starts_at - make_interval(secs => %s), ends_at = ends_at - make_interval(secs => %s) where user_id = %s", (secs, secs, uid))
        c.execute("update public.moadoom_subscriptions set starts_at = starts_at - make_interval(secs => %s), ends_at = ends_at - make_interval(secs => %s) where user_id = %s", (secs, secs, uid))
        row = c.execute("select public.moadoom_subscription_state(%s)", (uid,)).fetchone()[0]
    return row


@app.get("/__qa/mail-outbox")
def mail_outbox(to: str = ""):
    with db() as c:
        rows = c.execute("select to_email, kind, dedupe_key, status, attempts from public.moadoom_email_outbox where %s = '' or to_email = %s order by id", (to, to)).fetchall()
    return [dict(zip(["to", "kind", "dedupe_key", "status", "attempts"], r)) for r in rows]


@app.get("/site/assets/central/config.js")
def site_config():
    return Response(f'window.MOADOOM_CENTRAL = {{ url: "http://127.0.0.1:{args.port}", publishableKey: "{PUBLISHABLE}" }};',
                    media_type="application/javascript")


@app.get("/site/{path:path}")
def site(path: str):
    root = Path(args.site).resolve()
    p = (root / path).resolve()
    if p.is_dir():
        p = p / "index.html"
    if root not in p.parents and p != root or not p.exists():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(p)


@app.get("/health")
def health():
    return {"ok": True}


def main():
    global args, DB
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=54399)
    ap.add_argument("--site", required=True)
    ap.add_argument("--keys", required=True)
    ap.add_argument("--pgdata", default="C:/build/central-pg-data-qa")
    ap.add_argument("--node", default="node")
    args = ap.parse_args()
    srv = pgserver.get_server(args.pgdata, cleanup_mode=None)
    name = "qa_" + uuid.uuid4().hex[:10]
    with psycopg.connect(srv.get_uri(), autocommit=True) as c:
        c.execute(f"create database {name}")
    DB = srv.get_uri().rsplit("/", 1)[0] + "/" + name
    with db() as c:
        c.execute(SHIM.read_text(encoding="utf-8"))
        c.execute(MIGRATION.read_text(encoding="utf-8"))
    print(f"qa-emulator ready port={args.port} db={name}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=os.environ.get("QA_EMULATOR_LOG", "warning"))


if __name__ == "__main__":
    main()
