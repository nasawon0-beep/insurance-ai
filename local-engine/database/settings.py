"""로컬 앱 설정.

주민등록번호 입력은 2026-10-01 제품 결정에 따라 항상 활성이다. 과거
``RRN_INPUT_ENABLED`` 환경변수와 ``app_settings.rrn_input_enabled`` 값은 기존
DB/실행 환경에 남아 있어도 기능을 끄지 않는다. 이 결정은 암호화, 목록·매칭
응답 마스킹, 명시적 전체값 조회 사유 및 접근 로그를 제거하지 않는다.
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
    """항상 활성인 현재 제품 정책을 반환한다."""
    return True, "hardcoded"


def rrn_enabled(conn: sqlite3.Connection) -> bool:
    return True


def rrn_locked_by_env() -> bool:
    """기능은 제품 정책으로 고정되어 설정 API에서 변경할 수 없다."""
    return True


def set_rrn_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    """호환용 no-op. 과거 설정값이 현재 제품 정책을 덮어쓰지 못하게 한다."""
    return None
