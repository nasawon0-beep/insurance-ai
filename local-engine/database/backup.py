"""
고객 DB 자동/수동 백업 + 복구.

- 위치: resolve_db_path().parent / "backups"
- 파일명: customers-YYYYMMDD-HHMMSS.sqlite3
- 스냅샷: sqlite3.Connection.backup() API (열린 연결이 있어도 일관된 스냅샷).
  실패 시 파일 복사로 폴백.
- 보존: 최신 N=20 개 (customers-*-before-restore-* 은 보존 대상에서 제외, 절대 삭제 금지).
  유일본은 삭제하지 않는다. 디스크 풀이면 prune 하지 말고 에러를 던진다.
- 시작 시 하루 1회만 (오늘 날짜 백업 파일이 이미 있으면 건너뜀 — 파일명 기준).
- 복구: 선택 파일이 SQLite 헤더로 시작하지 않으면 거부.
  복구 전 항상 customers-before-restore-<ts>.sqlite3 안전 사본을 만든다.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import resolve_db_path

KEEP_N = int(os.environ.get("ENGINE_BACKUP_KEEP", "20"))
_SQLITE_MAGIC = b"SQLite format 3\x00"
_TS_RE = re.compile(r"customers-(\d{8})-(\d{6})(?:_\d+)?\.sqlite3$")
_SAFETY_PREFIX = "customers-before-restore-"


def backup_dir(db_path: Optional[str] = None) -> Path:
    d = resolve_db_path(db_path).parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _is_safety(name: str) -> bool:
    return name.startswith(_SAFETY_PREFIX)


def _pruneable(d: Path) -> list[Path]:
    """보존 정책 대상 (정규 스냅샷). 안전 사본은 제외."""
    out = [
        p for p in d.glob("customers-*.sqlite3")
        if p.is_file() and not _is_safety(p.name)
    ]
    out.sort(key=lambda p: p.name)  # 파일명에 타임스탬프가 있어 이름순 = 시간순
    return out


def list_backups(db_path: Optional[str] = None) -> list[dict]:
    d = backup_dir(db_path)
    items = []
    for p in d.glob("customers-*.sqlite3"):
        if not p.is_file():
            continue
        st = p.stat()
        items.append({
            "filename": p.name,
            "path": str(p),
            "size": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "kind": "safety" if _is_safety(p.name) else "snapshot",
        })
    items.sort(key=lambda x: x["filename"], reverse=True)
    return items


def should_backup_today(db_path: Optional[str] = None) -> bool:
    """오늘 날짜(로컬)의 정규 스냅샷이 이미 있으면 False."""
    today = datetime.now().strftime("%Y%m%d")
    for p in _pruneable(backup_dir(db_path)):
        m = _TS_RE.search(p.name)
        if m and m.group(1) == today:
            return False
    return True


def _snapshot(src: Path, dst: Path) -> None:
    """일관된 스냅샷. .backup() API 우선, 실패 시 파일 복사.

    어떤 실패든 잘린 dst 파일을 남기지 않는다 (헤더 16바이트만 맞아도
    restore 헤더검사를 통과해 '손상된 백업으로 복구'가 가능해지므로)."""
    try:
        src_conn = sqlite3.connect(str(src))
        try:
            dst_conn = sqlite3.connect(str(dst))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
    except sqlite3.OperationalError as e:
        # 디스크 풀 등은 그대로 올린다 (prune 로 지우지 않기 위해).
        if "disk" in str(e).lower() and "full" in str(e).lower():
            _rm(dst)
            raise
        _rm(dst)
        try:
            shutil.copy2(src, dst)
        except Exception:
            _rm(dst)
            raise
    except (sqlite3.DatabaseError, OSError):
        _rm(dst)
        raise


def _rm(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _prune(d: Path) -> int:
    files = _pruneable(d)
    if len(files) <= max(KEEP_N, 1):
        return 0
    to_delete = files[: len(files) - KEEP_N]
    # 유일본은 절대 삭제하지 않는다.
    if len(files) - len(to_delete) < 1:
        to_delete = to_delete[:-1]
    pruned = 0
    for p in to_delete:
        try:
            p.unlink()
            pruned += 1
        except OSError:
            pass
    return pruned


def make_backup(trigger: str = "manual", db_path: Optional[str] = None) -> dict:
    src = resolve_db_path(db_path)
    d = backup_dir(db_path)
    if not src.exists():
        # 아직 DB 가 없으면 빈 스냅샷을 만들지 않는다.
        return {"created": False, "reason": "no source db", "pruned": 0, "trigger": trigger}
    base = f"customers-{_ts()}"
    dst = d / f"{base}.sqlite3"
    n = 1
    while dst.exists():  # 같은 초 안에 여러 번 눌러도 안 겹치게
        dst = d / f"{base}_{n:03d}.sqlite3"
        n += 1
    _snapshot(src, dst)
    pruned = _prune(d)
    st = dst.stat()
    return {
        "created": True,
        "filename": dst.name,
        "path": str(dst),
        "size": st.st_size,
        "pruned": pruned,
        "trigger": trigger,
        "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    }


def startup_backup(db_path: Optional[str] = None) -> Optional[dict]:
    """부팅 훅. ENGINE_BACKUP=0 이면 아무것도 안 함. 하루 1회."""
    if os.environ.get("ENGINE_BACKUP", "1") != "1":
        return None
    if not should_backup_today(db_path):
        return None
    return make_backup("startup", db_path)


def restore_backup(filename: str, db_path: Optional[str] = None) -> dict:
    d = backup_dir(db_path)
    # 경로 조작 방지 — 파일명만 허용.
    if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
        raise ValueError("잘못된 파일명입니다.")
    src = d / filename
    if not src.is_file():
        raise FileNotFoundError("백업 파일을 찾을 수 없습니다.")
    with open(src, "rb") as f:
        head = f.read(16)
    if not head.startswith(_SQLITE_MAGIC):
        raise ValueError("SQLite 데이터베이스 파일이 아닙니다. 복구를 취소했습니다.")

    target = resolve_db_path(db_path)
    safety = d / f"{_SAFETY_PREFIX}{_ts()}.sqlite3"
    if target.exists():
        _snapshot(target, safety)
    else:
        safety.write_bytes(b"")

    # 현재 DB 를 선택한 백업으로 덮어쓴다 — 원자적으로.
    # 같은 디렉터리에 임시 복사 후 os.replace 로 교체하면, 복사 도중 실패해도
    # 라이브 DB(customers.sqlite3)는 손상되지 않는다.
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".restore-tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, target)
    except Exception:
        _rm(tmp)
        raise
    return {
        "restored": filename,
        "safety_copy": safety.name,
        "restart_required": True,
    }
