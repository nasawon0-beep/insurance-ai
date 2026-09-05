"""고객 DB 백업/복구 테스트 (database/backup.py)."""
import base64
import sqlite3

import pytest

_KEY = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def env(tmp_path, monkeypatch):
    from database import crypto

    dbp = tmp_path / "data" / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(dbp))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _KEY)
    monkeypatch.setenv("ENGINE_BACKUP", "0")  # 부팅 훅 조용히
    crypto.reset_cache()
    yield dbp
    crypto.reset_cache()


def _seed_db(dbp):
    from database.db import connect, init_schema

    conn = connect(str(dbp))
    init_schema(conn)
    conn.execute(
        "INSERT INTO customers (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("c1", "enc", "2026-01-01", "2026-01-01"),
    )
    conn.commit()
    return conn


def test_make_and_list(env):
    _seed_db(env).close()
    from database import backup

    r = backup.make_backup("manual")
    assert r["created"] and r["filename"].startswith("customers-")
    lst = backup.list_backups()
    assert len(lst) == 1 and lst[0]["filename"] == r["filename"]
    assert lst[0]["kind"] == "snapshot"


def test_prune_keeps_20(env, monkeypatch):
    _seed_db(env).close()
    from database import backup

    monkeypatch.setattr(backup, "KEEP_N", 20)
    made = []
    for _ in range(21):
        made.append(backup.make_backup("manual"))
    snaps = [b for b in backup.list_backups() if b["kind"] == "snapshot"]
    assert len(snaps) == 20
    # 마지막 prune 호출이 1개 지웠다고 보고
    assert made[-1]["pruned"] == 1


def test_prune_excludes_safety_and_never_deletes_only_copy(env, monkeypatch):
    _seed_db(env).close()
    from database import backup

    monkeypatch.setattr(backup, "KEEP_N", 1)
    backup.make_backup("manual")
    # 복구로 안전 사본 생성
    first = backup.list_backups()[0]["filename"]
    backup.restore_backup(first)
    backup.make_backup("manual")
    kinds = {b["filename"]: b["kind"] for b in backup.list_backups()}
    assert any(k == "safety" for k in kinds.values())  # 안전 사본은 보존
    assert sum(1 for v in kinds.values() if v == "snapshot") >= 1


def test_should_backup_today(env):
    _seed_db(env).close()
    from database import backup

    assert backup.should_backup_today() is True
    backup.make_backup("startup")
    assert backup.should_backup_today() is False


def test_restore_writes_safety_copy(env):
    conn = _seed_db(env)
    conn.close()
    from database import backup

    snap = backup.make_backup("manual")["filename"]
    # 원본 DB 를 바꾼다
    conn = sqlite3.connect(str(env))
    conn.execute(
        "INSERT INTO customers (id, name, created_at, updated_at) VALUES ('c2','x','t','t')"
    )
    conn.commit()
    conn.close()

    r = backup.restore_backup(snap)
    assert r["restart_required"] is True
    assert r["safety_copy"].startswith("customers-before-restore-")
    # 복구 후엔 c2 가 없어야 (스냅샷 시점으로 롤백)
    conn = sqlite3.connect(str(env))
    n = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    conn.close()
    assert n == 1
    # 안전 사본은 c2 를 담고 있다
    from database.backup import backup_dir

    sc = sqlite3.connect(str(backup_dir() / r["safety_copy"]))
    assert sc.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2
    sc.close()


def test_restore_rejects_non_sqlite(env):
    _seed_db(env).close()
    from database import backup
    from database.backup import backup_dir

    bad = backup_dir() / "customers-20260101-000000.sqlite3"
    bad.write_bytes(b"NOT A SQLITE FILE" + b"\x00" * 32)
    with pytest.raises(ValueError):
        backup.restore_backup(bad.name)


def test_backup_consistency_with_open_connection(env):
    conn = _seed_db(env)  # 연결을 열어둔 채
    from database import backup

    conn.execute(
        "INSERT INTO customers (id, name, created_at, updated_at) VALUES ('c9','y','t','t')"
    )
    conn.commit()
    r = backup.make_backup("manual")
    snap = sqlite3.connect(r["path"])
    got = snap.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    snap.close()
    conn.close()
    assert got == 2  # 열린 연결이 있어도 커밋된 상태가 일관되게 스냅샷됨


def test_snapshot_leaves_no_partial_file_on_failure(env, tmp_path, monkeypatch):
    from database import backup

    # .backup() 이 '디스크 풀' 로 실패하는 상황을 흉내낸다 (파일-복사 폴백도 실패).
    class _FakeConn:
        def backup(self, *a, **k):
            raise __import__("sqlite3").OperationalError("database or disk is full")
        def close(self):
            pass

    monkeypatch.setattr(backup.sqlite3, "connect", lambda *a, **k: _FakeConn())
    src = tmp_path / "src.sqlite3"
    src.write_bytes(b"SQLite format 3\x00" + b"x" * 100)
    dst = backup.backup_dir() / "customers-20260101-010101.sqlite3"
    dst.write_bytes(b"SQLite format 3\x00" + b"partial")  # 잘린 부분 파일이 이미 있다고 가정
    with pytest.raises(Exception):
        backup._snapshot(src, dst)
    assert not dst.exists()  # 부분 파일을 지워 restore 헤더검사를 오도하지 않는다


def test_restore_is_atomic_and_db_stays_valid(env):
    conn = _seed_db(env)
    conn.close()
    from database import backup

    snap = backup.make_backup("manual")["filename"]
    r = backup.restore_backup(snap)
    # 복구 후 라이브 DB 가 열리고 읽힌다 (원자적 os.replace)
    conn = sqlite3.connect(str(env))
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1
    conn.close()
    assert not (env.parent / (env.name + ".restore-tmp")).exists()
    assert r["restart_required"] is True
