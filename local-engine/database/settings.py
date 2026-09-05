"""
로컬 앱 설정 (app_settings 테이블) + RRN 입력 토글의 유효값 계산.

RRN 파일럿 토글:
  - 환경변수 RRN_INPUT_ENABLED 이 "0" 또는 "1" 이면 그 값이 최우선 (source="env", 잠금).
  - 아니면 app_settings 의 "rrn_input_enabled" ("1"/"0") 사용 (source="local").
  - 둘 다 없으면 기본 False (source="default").

control-server / 텔레메트리와 무관하다. 순수 로컬.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple

_RRN_KEY = "rrn_input_enabled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def set_setting(conn: sqlite3.Connection, key: str, value: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _now()),
    )
    conn.commit()


def _env_forced() -> Optional[bool]:
    v = os.environ.get("RRN_INPUT_ENABLED")
    if v in ("0", "1"):
        return v == "1"
    return None


def rrn_setting(conn: sqlite3.Connection) -> Tuple[bool, str]:
    """(유효값, source) — source ∈ {"env","local","default"}."""
    forced = _env_forced()
    if forced is not None:
        return forced, "env"
    local = get_setting(conn, _RRN_KEY)
    if local in ("0", "1"):
        return local == "1", "local"
    return False, "default"


def rrn_enabled(conn: sqlite3.Connection) -> bool:
    return rrn_setting(conn)[0]


def rrn_locked_by_env() -> bool:
    return _env_forced() is not None


def set_rrn_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    set_setting(conn, _RRN_KEY, "1" if enabled else "0")
