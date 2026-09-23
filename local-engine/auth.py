"""로컬 엔진 마스터 비밀번호와 세션 관리."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from auth_schema import init_auth_schema

router = APIRouter(prefix="/auth", tags=["auth"])

auth_db_path = Path(__file__).parent / "database" / "data" / "auth.sqlite3"
pwd_mgr = None
session_mgr = None
_SESSION_TTL = timedelta(minutes=30)
_PBKDF2_ROUNDS = 200_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _connect() -> sqlite3.Connection:
    auth_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(auth_db_path), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        init_auth_schema(conn)
        return conn
    except Exception:
        conn.close()
        raise


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ROUNDS,
        salt.hex(),
        digest.hex(),
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


class PasswordManager:
    def is_initialized(self) -> bool:
        with _connect() as conn:
            return conn.execute("SELECT 1 FROM password WHERE id = 1").fetchone() is not None

    def setup(self, password: str) -> None:
        with _connect() as conn:
            if conn.execute("SELECT 1 FROM password WHERE id = 1").fetchone():
                raise ValueError("마스터 비밀번호가 이미 설정되어 있습니다.")
            conn.execute("INSERT INTO password (id, hash) VALUES (1, ?)", (_hash_password(password),))
            conn.commit()

    def verify(self, password: str) -> bool:
        with _connect() as conn:
            row = conn.execute("SELECT hash FROM password WHERE id = 1").fetchone()
        return bool(row and _verify_password(password, row["hash"]))


class SessionManager:
    def cleanup_expired_sessions(self) -> int:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now_iso(),))
            conn.commit()
            return cur.rowcount

    def create_session(self, device_id: str = "") -> str:
        self.cleanup_expired_sessions()
        sid = secrets.token_urlsafe(32)
        now = _now()
        expires = now + _SESSION_TTL
        with _connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, device_id, created_at, last_accessed_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, device_id, now.isoformat(), now.isoformat(), expires.isoformat()),
            )
            conn.commit()
        return sid

    def validate_session(self, session_id: str) -> bool:
        self.cleanup_expired_sessions()
        if not session_id:
            return False
        expires = (_now() + _SESSION_TTL).isoformat()
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET last_accessed_at = ?, expires_at = ? WHERE id = ?",
                (_now_iso(), expires, session_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def invalidate_session(self, session_id: str) -> None:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()

    def invalidate_all_sessions(self) -> None:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions")
            conn.commit()


def _pwd_mgr() -> PasswordManager:
    global pwd_mgr
    if pwd_mgr is None:
        pwd_mgr = PasswordManager()
    return pwd_mgr


def _session_mgr() -> SessionManager:
    global session_mgr
    if session_mgr is None:
        session_mgr = SessionManager()
    return session_mgr


@router.get("/status")
def auth_status():
    return {"initialized": _pwd_mgr().is_initialized()}


@router.post("/setup")
def auth_setup(body: dict):
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 8자 이상이어야 합니다.")
    try:
        _pwd_mgr().setup(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}


@router.post("/login")
def auth_login(body: dict):
    password = str(body.get("password") or "")
    if not _pwd_mgr().verify(password):
        raise HTTPException(status_code=401, detail="비밀번호가 틀렸습니다.")
    sid = _session_mgr().create_session(str(body.get("device_id") or ""))
    return {"session_id": sid}


@router.post("/logout")
def auth_logout(x_session_id: Optional[str] = Header(None)):
    if x_session_id:
        _session_mgr().invalidate_session(x_session_id)
    return {"success": True}


@router.post("/change-password")
def change_password(body: dict, x_session_id: Optional[str] = Header(None)):
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="비밀번호는 8자 이상이어야 합니다.")
    if not _pwd_mgr().verify(current):
        raise HTTPException(status_code=401, detail="비밀번호가 틀렸습니다.")
    with _connect() as conn:
        conn.execute("UPDATE password SET hash = ? WHERE id = 1", (_hash_password(new),))
        conn.commit()
    _session_mgr().invalidate_all_sessions()
    if x_session_id:
        _session_mgr().invalidate_session(x_session_id)
    return {"success": True}
