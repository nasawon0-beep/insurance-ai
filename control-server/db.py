"""
control-server 저장소 (SQLite). 최소 권한 — 고객 데이터는 절대 안 들어온다.
저장 항목(문서 24): user_id, email, plan, status, device_id, app_version, license_expiry.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_DEFAULT = Path(__file__).parent / "data" / "control.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    pw_hash    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS licenses (
    user_id      TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan         TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE | PRO
    device_limit INTEGER NOT NULL DEFAULT 2,
    expiry       TEXT,                             -- ISO date; NULL = 무기한
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id     TEXT NOT NULL,      -- 클라이언트가 만든 안정적 기기 식별자
    name          TEXT,
    app_version   TEXT,
    registered_at TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    UNIQUE(user_id, device_id)
);

CREATE TABLE IF NOT EXISTS error_reports (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    app_version TEXT,
    code        TEXT,
    message     TEXT,               -- PII 없어야 함 (클라이언트 책임)
    created_at  TEXT NOT NULL
);
"""


def db_path() -> Path:
    configured = os.environ.get("CONTROL_DB_PATH")
    if configured:
        return Path(configured)
    if getattr(sys, "_MEIPASS", None):
        raise RuntimeError("CONTROL_DB_PATH must be set for the packaged control-server")
    return _DEFAULT


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
