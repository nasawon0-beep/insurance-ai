"""
사용 로그 (기능 사용량 파악). PII 없음 — 화이트리스트된 이벤트명 + 이벤트별 허용 prop 키/타입만.
자유 텍스트·이름·내용은 저장 불가. 텔레메트리/자동전송 없음 — 수동 CSV 내보내기만.

startup 에서 180일 초과 행을 정리한다.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./{}:-]{1,64}$")
_MAX_INT = 10 ** 9

# 타입 마커
_BOOL = "bool"
_INT = "int"
_TOKEN = "token"  # 짧은 식별자성 문자열 (엔드포인트 템플릿, 필드명 등) — PII 아님


# 이벤트 화이트리스트: event -> {prop_key: spec}
# spec 은 _BOOL / _INT / _TOKEN 또는 허용값 set(열거형).
EVENTS: dict[str, dict[str, Any]] = {
    "app_open": {},
    "login": {"ok": _BOOL},
    "capture_run": {"source": {"text", "audio", "pdf"}, "items": _INT},
    "capture_saved": {"created": _INT, "merged": _INT, "skipped": _INT, "policies": _INT},
    "capture_undone": {"customers": _INT, "policies": _INT, "consultations": _INT},
    "customer_created": {"via": {"form", "capture", "bulk", "import"}},
    "customer_updated": {"fields_changed": _INT},
    "customer_deleted": {},
    "policy_saved": {"count": _INT, "from": {"manual", "document"}},
    "consultation_added": {"channel_kind": {"visit", "call", "doc", "other"}, "has_audio": _BOOL},
    "doc_captured": {
        "doc_type": {"보장분석", "가입제안서", "보험증권", "청약서", "보험문서"},
        "policies": _INT,
        "coverages": _BOOL,
    },
    "assistant_asked": {"history_len": _INT},
    "reminder_acted": {"kind": {"follow_up_complete", "birthday_open", "expiry_open"}},
    "field_corrected": {"entity": {"customer", "policy"}, "field": _TOKEN},
    "export_run": {"format": {"csv", "zip"}, "rows": _INT, "include_rrn": _BOOL},
    "import_run": {"created": _INT, "merged": _INT, "failed": _INT},
    "backup_made": {"trigger": {"startup", "manual"}, "pruned": _INT},
    "restore_run": {},
    "error": {
        "where": {"react", "engine", "network", "import", "export", "backup"},
        "status": _INT,
    },
}


def _clean_value(spec: Any, value: Any) -> Optional[Any]:
    if isinstance(spec, set):
        return value if isinstance(value, str) and value in spec else None
    if spec == _BOOL:
        return bool(value) if isinstance(value, bool) else None
    if spec == _INT:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return max(0, min(value, _MAX_INT))
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return max(0, min(int(value), _MAX_INT))
        return None
    if spec == _TOKEN:
        return value if isinstance(value, str) and _TOKEN_RE.match(value) else None
    return None


def sanitize(event: str, props: Optional[dict]) -> Optional[dict]:
    """화이트리스트 밖 이벤트면 None. 아니면 허용 키만 남긴 dict (빈 dict 가능)."""
    if event not in EVENTS:
        return None
    allowed = EVENTS[event]
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if k not in allowed:
            continue
        cleaned = _clean_value(allowed[k], v)
        if cleaned is not None:
            out[k] = cleaned
    return out


def record(
    conn: sqlite3.Connection,
    event: str,
    props: Optional[dict] = None,
    device_id: Optional[str] = None,
    app_version: Optional[str] = None,
) -> bool:
    clean = sanitize(event, props)
    if clean is None:
        return False
    did = device_id if (isinstance(device_id, str) and _TOKEN_RE.match(device_id)) else None
    ver = app_version if (isinstance(app_version, str) and _TOKEN_RE.match(app_version)) else None
    conn.execute(
        "INSERT INTO usage_log (id, event, props, device_id, app_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex,
            event,
            json.dumps(clean, ensure_ascii=False) if clean else None,
            did,
            ver,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return True


def prune(conn: sqlite3.Connection, days: int = 180) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM usage_log WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def export_csv(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    mode: str = "raw",
) -> str:
    where = []
    params: list[Any] = []
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if until:
        where.append("created_at <= ?")
        params.append(until)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    buf = io.StringIO()
    w = csv.writer(buf)
    if mode == "daily":
        w.writerow(["event", "day", "count"])
        rows = conn.execute(
            f"SELECT event, substr(created_at, 1, 10) AS day, COUNT(*) AS c "
            f"FROM usage_log{clause} GROUP BY event, day ORDER BY day, event",
            params,
        ).fetchall()
        for r in rows:
            w.writerow([r["event"], r["day"], r["c"]])
    else:
        w.writerow(["event", "ts", "props_json", "device_id"])
        rows = conn.execute(
            f"SELECT event, created_at, props, device_id FROM usage_log{clause} "
            f"ORDER BY created_at",
            params,
        ).fetchall()
        for r in rows:
            w.writerow([r["event"], r["created_at"], r["props"] or "", r["device_id"] or ""])
    return buf.getvalue()
