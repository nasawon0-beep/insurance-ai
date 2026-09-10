"""
control-server — 로그인 / 라이선스 / 기기 / 업데이트 / 오류수집 (최소 권한).

아키텍처 3번: 고객 데이터는 절대 저장하지 않는다.
저장 항목: user_id, email, plan, status, device_id, app_version, license_expiry.

기본 포트 8790. 배포 시 CONTROL_JWT_SECRET / CONTROL_LICENSE_SECRET 반드시 주입.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from db import connect, init
from models import Credentials, DeviceIn, ErrorReportIn, LicensePatch
from security import (
    decode_token,
    hash_password,
    make_token,
    sign_license,
    verify_password,
)

import asyncio
import contextlib


def _lock_is_free(lock_path: str) -> bool:
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False  # 파일 없음/못 엶 — 판단 보류
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False  # 앱이 잡고 있음 = 정상
    finally:
        os.close(fd)
    return True  # 우리가 잡음 = 앱이 놨다 = 앱 종료


@contextlib.asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    lock_path = os.environ.get("APP_LOCK_FILE", "")
    task = None
    if lock_path:
        print(f"control-server: watching parent lock {lock_path!r}", file=sys.stderr)

        async def _watch() -> None:
            # 부모(앱) 종료 시 stderr 파이프의 read end 가 닫혀 이후 어떤 print 든 블록/EPIPE 된다.
            # 그래서 감지 후엔 I/O 없이 바로 os._exit(0). (심장박동·안내 로그 넣지 말 것)
            while True:
                await asyncio.sleep(2)
                if _lock_is_free(lock_path):
                    os._exit(0)

        task = asyncio.ensure_future(_watch())
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Insurance AI Control Server", lifespan=_lifespan)
_DEFAULT_ORIGINS = ",".join(
    [
        # 데스크톱 앱 (Tauri 런타임 + vite 개발 서버)
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        # 관리 콘솔 (별도 vite)
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
)
ADMIN_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CONTROL_ADMIN_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ADMIN_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

TRIAL_DAYS = int(os.environ.get("CONTROL_TRIAL_DAYS", "30"))


def _resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return root / name


UPDATE_MANIFEST = _resource_path("update_manifest.json")
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
_FWD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _looks_like_ip(host: str) -> bool:
    return bool(host) and all(c in "0123456789.:abcdefABCDEF" for c in host)


def _local_recovery_enabled() -> bool:
    return os.environ.get("CONTROL_LOCAL_RECOVERY", "").strip() == "1"


def require_local_recovery(request: Request) -> None:
    client = request.client
    peer = client.host if client else "unknown"
    if not _local_recovery_enabled():
        print(f"local-recovery denied: peer={peer} reason=flag", file=sys.stderr)
        raise HTTPException(status_code=404)
    if any(h in request.headers for h in _FWD_HEADERS):
        print(f"local-recovery denied: peer={peer} reason=forwarded", file=sys.stderr)
        raise HTTPException(status_code=404)
    if client is None or client.host not in _LOOPBACK:
        print(f"local-recovery denied: peer={peer} reason=peer", file=sys.stderr)
        raise HTTPException(status_code=404)
    srv = request.scope.get("server") or ("", 0)
    host = srv[0] or ""
    if host and _looks_like_ip(host) and host not in _LOOPBACK:
        print(f"local-recovery denied: peer={peer} reason=bind", file=sys.stderr)
        raise HTTPException(status_code=404)


def get_conn():
    conn = connect()
    try:
        init(conn)
        yield conn
    finally:
        conn.close()


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    payload = decode_token(authorization.split(" ", 1)[1])
    if not payload:
        raise HTTPException(status_code=401, detail="토큰이 유효하지 않거나 만료되었습니다.")
    return payload


def require_admin(x_control_admin_token: Optional[str] = Header(None)) -> None:
    admin_token = os.environ.get("CONTROL_ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="관리자 인증이 설정되지 않았습니다.")
    if not x_control_admin_token or not secrets.compare_digest(
        x_control_admin_token, admin_token
    ):
        raise HTTPException(status_code=401, detail="관리자 인증이 필요합니다.")


# ---------- auth ----------

def _issue(conn, row) -> dict:
    return {
        "token": make_token(row["id"], row["email"]),
        "user": {"id": row["id"], "email": row["email"]},
    }


@app.post("/auth/register")
def register(body: Credentials, conn=Depends(get_conn)):
    email = body.email.strip().lower()
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")
    uid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO users (id, email, pw_hash, created_at) VALUES (?, ?, ?, ?)",
        (uid, email, hash_password(body.password), _now()),
    )
    expiry = (date.today() + timedelta(days=TRIAL_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO licenses (user_id, plan, device_limit, expiry, updated_at) "
        "VALUES (?, 'ACTIVE', 2, ?, ?)",
        (uid, expiry, _now()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return _issue(conn, row)


@app.post("/auth/login")
def login(body: Credentials, conn=Depends(get_conn)):
    email = body.email.strip().lower()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(body.password, row["pw_hash"]):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    return _issue(conn, row)


@app.get("/auth/me")
def me(user=Depends(current_user), conn=Depends(get_conn)):
    row = conn.execute(
        "SELECT id, email, created_at FROM users WHERE id = ?", (user["sub"],)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return {
        "user": {
            "id": row["id"],
            "email": row["email"],
            "created_at": row["created_at"],
        }
    }


@app.post("/auth/change-password")
def change_password(body: dict, user=Depends(current_user), conn=Depends(get_conn)):
    """로그인된 상태에서 비밀번호 변경 (현재 비번 확인 후)."""
    cur_pw = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "")
    if len(new_pw) < 6:
        raise HTTPException(status_code=422, detail="새 비밀번호는 6자 이상이어야 합니다.")
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user["sub"],)).fetchone()
    if not row or not verify_password(cur_pw, row["pw_hash"]):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")
    conn.execute(
        "UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new_pw), user["sub"])
    )
    conn.commit()
    return {"changed": True}


@app.get("/auth/local-recovery/status")
def local_recovery_status(_=Depends(require_local_recovery)):
    return {"available": True}


@app.get("/auth/local-recovery/accounts")
def local_recovery_accounts(_=Depends(require_local_recovery), conn=Depends(get_conn)):
    rows = conn.execute("SELECT email, created_at FROM users ORDER BY created_at").fetchall()
    return {"accounts": [dict(row) for row in rows]}


@app.post("/auth/local-recovery/reset")
def local_recovery_reset(
    body: dict,
    request: Request,
    _=Depends(require_local_recovery),
    conn=Depends(get_conn),
):
    email = str(body.get("email") or "").strip().lower()
    new_pw = str(body.get("new_password") or "")
    if len(new_pw) < 6:
        raise HTTPException(status_code=422, detail="새 비밀번호는 6자 이상이어야 합니다.")
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="해당 이메일의 계정을 찾을 수 없습니다.")
    conn.execute(
        "UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new_pw), row["id"])
    )
    conn.commit()
    print(
        f"local-recovery reset: {email} peer={request.client.host} at {_now()}",
        file=sys.stderr,
    )
    return {"reset": True, "email": email}


# ---------- license ----------

def _license_state(conn, uid: str) -> dict:
    lic = conn.execute("SELECT * FROM licenses WHERE user_id = ?", (uid,)).fetchone()
    if not lic:
        raise HTTPException(status_code=404, detail="라이선스를 찾을 수 없습니다.")
    expiry = lic["expiry"]
    days_left = None
    status = "active"
    if expiry:
        days_left = (date.fromisoformat(expiry) - date.today()).days
        status = "active" if days_left >= 0 else "expired"
    return {
        "plan": lic["plan"],
        "device_limit": lic["device_limit"],
        "expiry": expiry,
        "status": status,
        "days_left": days_left,
    }


@app.get("/license")
def get_license(user=Depends(current_user), conn=Depends(get_conn)):
    state = _license_state(conn, user["sub"])
    # 오프라인 유예용 서명 블롭 동봉 — 클라이언트가 저장해두고 오프라인 시 사용
    return {**state, "signed": sign_license({**state, "user_id": user["sub"]})}


@app.patch("/admin/license/{user_id}")
def set_license(
    user_id: str,
    body: LicensePatch,
    _admin=Depends(require_admin),
    conn=Depends(get_conn),
):
    """관리자 수동 라이선스 부여 (지금은 결제 없음 — 문서: 관리자가 직접)."""
    if not conn.execute("SELECT 1 FROM licenses WHERE user_id = ?", (user_id,)).fetchone():
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    fields = body.model_dump(exclude_unset=True)
    if "expiry" in fields and fields["expiry"] == "":
        fields["expiry"] = None
    if fields:
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE licenses SET {sets} WHERE user_id = ?", [*fields.values(), user_id]
        )
        conn.commit()
    return _license_state(conn, user_id)


# ---------- devices ----------

@app.post("/devices", status_code=201)
def register_device(body: DeviceIn, user=Depends(current_user), conn=Depends(get_conn)):
    uid = user["sub"]
    existing = conn.execute(
        "SELECT id FROM devices WHERE user_id = ? AND device_id = ?", (uid, body.device_id)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE devices SET name = ?, app_version = ?, last_seen = ? WHERE id = ?",
            (body.name, body.app_version, _now(), existing["id"]),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM devices WHERE id = ?", (existing["id"],)
        ).fetchone()

    limit = conn.execute(
        "SELECT device_limit FROM licenses WHERE user_id = ?", (uid,)
    ).fetchone()["device_limit"]
    count = conn.execute(
        "SELECT COUNT(*) FROM devices WHERE user_id = ?", (uid,)
    ).fetchone()[0]
    if count >= limit:
        raise HTTPException(
            status_code=409,
            detail=f"등록 가능한 기기 수({limit}대)를 초과했습니다. 기존 기기를 해제하세요.",
        )
    did = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO devices (id, user_id, device_id, name, app_version, registered_at, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (did, uid, body.device_id, body.name, body.app_version, _now(), _now()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM devices WHERE id = ?", (did,)).fetchone()


@app.get("/devices")
def list_devices(user=Depends(current_user), conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM devices WHERE user_id = ? ORDER BY registered_at", (user["sub"],)
    ).fetchall()
    return {"devices": [dict(r) for r in rows]}


@app.delete("/devices/{did}", status_code=204)
def delete_device(did: str, user=Depends(current_user), conn=Depends(get_conn)):
    cur = conn.execute(
        "DELETE FROM devices WHERE id = ? AND user_id = ?", (did, user["sub"])
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="기기를 찾을 수 없습니다.")


# ---------- update ----------

@app.get("/update/check")
def update_check(version: str = "", channel: str = "STABLE"):
    if UPDATE_MANIFEST.exists():
        manifest = json.loads(UPDATE_MANIFEST.read_text(encoding="utf-8"))
    else:
        manifest = {"STABLE": {"latest": "0.1.0", "url": None, "mandatory": False, "notes": ""}}
    ch = manifest.get(channel, manifest.get("STABLE", {}))
    ch["up_to_date"] = version == ch.get("latest")
    return ch


# ---------- error report (PII-safe) ----------

@app.post("/error-report", status_code=201)
def error_report(
    body: ErrorReportIn,
    authorization: Optional[str] = Header(None),
    conn=Depends(get_conn),
):
    uid = None
    if authorization and authorization.lower().startswith("bearer "):
        p = decode_token(authorization.split(" ", 1)[1])
        uid = p["sub"] if p else None
    conn.execute(
        "INSERT INTO error_reports (id, user_id, app_version, code, message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, uid, body.app_version, body.code, body.message, _now()),
    )
    conn.commit()
    return {"received": True}


@app.get("/health")
def health():
    return {"control_server": "ok"}


def _port_taken(host: str, port: int) -> bool:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _healthy(host: str, port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CONTROL_PORT", "8790"))
    # host 를 설정 가능하게 바꿀 때를 대비한 가드 — 현재 리터럴이라 비활성.
    host = "127.0.0.1"
    if host not in _LOOPBACK:
        os.environ["CONTROL_LOCAL_RECOVERY"] = "0"
        print("control-server: local-recovery disabled (non-loopback bind)", file=sys.stderr)

    # 이미 정상 인스턴스가 점유 중이면 재사용(개발 스크립트 중복 기동·재시작 충돌 방지).
    if _port_taken(host, port):
        if _healthy(host, port):
            print(f"control-server: :{port} already serving — reusing", file=sys.stderr)
            sys.exit(0)
        print(f"control-server: :{port} taken by an unhealthy process — exiting", file=sys.stderr)
        sys.exit(1)
    # APP_LOCK_FILE 감시는 _lifespan 에서 (asyncio 태스크 — onefile 스레드 이슈 회피)

    uvicorn.run(app, host=host, port=port)
