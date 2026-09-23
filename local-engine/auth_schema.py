"""로컬 엔진 인증 DB 스키마."""
from __future__ import annotations

import sqlite3

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS password (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT,
    created_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def init_auth_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(AUTH_SCHEMA)
    conn.commit()
