"""시작 유지보수 상태와 /health 노출 테스트."""
import base64
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

_KEY = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def maintenance_env(tmp_path, monkeypatch):
    import main
    from database import crypto

    dbp = tmp_path / "data" / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(dbp))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _KEY)
    monkeypatch.setenv("ENGINE_WARMUP", "0")
    monkeypatch.setenv("WHISPER_PREWARM", "0")
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    main._MAINT_STATUS["backup"] = {"state": "pending"}
    main._MAINT_STATUS["usage_prune"] = {"state": "pending"}
    crypto.reset_cache()
    yield main, dbp
    crypto.reset_cache()


def _health_details(main):
    return TestClient(main.app).get("/health/details")


def test_api_secret_allows_windows_webview_null_origin(maintenance_env):
    main, _ = maintenance_env
    response = TestClient(main.app).get("/api-secret", headers={"Origin": "null"})
    assert response.status_code == 200
    assert response.json()["secret"]


def test_api_secret_allows_native_clients_without_origin(maintenance_env):
    main, _ = maintenance_env
    response = TestClient(main.app).get("/api-secret")
    assert response.status_code == 200
    assert response.json()["secret"]


def test_backup_failure_is_reported_and_health_stays_up(maintenance_env, monkeypatch):
    main, dbp = maintenance_env
    from database import backup
    from database.db import connect, init_schema

    conn = connect(str(dbp))
    init_schema(conn)
    conn.close()
    monkeypatch.setenv("ENGINE_BACKUP", "1")
    monkeypatch.setattr(backup, "make_backup", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    main._startup_maintenance()
    response = TestClient(main.app).get("/health/details")
    maintenance = response.json()["maintenance"]
    assert response.status_code == 200
    assert maintenance["backup_startup"]["state"] == "error"
    assert maintenance["backup_startup"]["error"] == "disk full"
    assert maintenance["warning"] is True


def test_usage_prune_failure_does_not_stop_startup(maintenance_env, monkeypatch):
    main, _ = maintenance_env
    from database import usage

    monkeypatch.setenv("ENGINE_BACKUP", "0")
    monkeypatch.setattr(usage, "prune", lambda conn: (_ for _ in ()).throw(RuntimeError("prune failed")))
    main._startup_maintenance()

    response = TestClient(main.app).get("/health/details")
    assert response.status_code == 200
    status = response.json()["maintenance"]["usage_prune"]
    assert status["state"] == "error"
    assert status["error"] == "prune failed"


def test_recent_backup_has_no_warning(maintenance_env, monkeypatch):
    main, dbp = maintenance_env
    from database import backup
    from database.db import connect, init_schema

    conn = connect(str(dbp))
    init_schema(conn)
    conn.close()
    backup.make_backup("manual")
    monkeypatch.setenv("ENGINE_BACKUP", "1")
    main._startup_maintenance()

    maintenance = TestClient(main.app).get("/health/details").json()["maintenance"]
    assert maintenance["backup_startup"]["state"] in ("ok", "skipped")
    assert maintenance["last_backup_at"] is not None
    assert "warning" not in maintenance


def test_stale_backup_warns(maintenance_env, monkeypatch):
    main, dbp = maintenance_env
    from database import backup
    from database.db import connect, init_schema

    conn = connect(str(dbp))
    init_schema(conn)
    conn.close()
    old = datetime.now() - timedelta(days=3, minutes=1)
    path = backup.backup_dir() / f"customers-{old:%Y%m%d}-120000.sqlite3"
    path.write_bytes(b"SQLite format 3\x00")
    os.utime(path, (old.timestamp(), old.timestamp()))
    monkeypatch.setenv("ENGINE_BACKUP", "1")
    monkeypatch.setattr(backup, "startup_backup", lambda: None)
    main._startup_maintenance()

    maintenance = TestClient(main.app).get("/health/details").json()["maintenance"]
    assert maintenance["warning"] is True
    assert maintenance["last_backup_age_days"] >= 3


def test_disabled_backup_does_not_warn(maintenance_env, monkeypatch):
    main, _ = maintenance_env
    monkeypatch.setenv("ENGINE_BACKUP", "0")
    main._startup_maintenance()

    maintenance = TestClient(main.app).get("/health/details").json()["maintenance"]
    assert maintenance["backup_startup"]["disabled"] is True
    assert "warning" not in maintenance


def test_fresh_install_retries_backup_after_migration(maintenance_env, monkeypatch):
    main, dbp = maintenance_env

    monkeypatch.setenv("ENGINE_BACKUP", "1")
    assert not dbp.exists()
    assert not (dbp.parent / "backups").exists()

    main._startup_maintenance()

    maintenance = TestClient(main.app).get("/health/details").json()["maintenance"]
    assert dbp.exists()
    assert maintenance["backup_startup"]["state"] == "ok"
    assert maintenance["last_backup_at"] is not None
    assert "warning" not in maintenance


def test_health_ignores_safety_copy_for_last_backup(maintenance_env):
    main, dbp = maintenance_env
    from database import backup

    backups = backup.backup_dir(str(dbp))
    snapshot = backups / f"customers-{datetime.now():%Y%m%d}-120000.sqlite3"
    snapshot.write_bytes(b"SQLite format 3\x00")
    safety = backups / "customers-before-restore-20990101-120000.sqlite3"
    safety.write_bytes(b"SQLite format 3\x00")
    old = datetime.now() - timedelta(days=3, minutes=1)
    os.utime(safety, (old.timestamp(), old.timestamp()))

    maintenance = TestClient(main.app).get("/health/details").json()["maintenance"]
    assert maintenance["last_backup_at"] == datetime.fromtimestamp(
        snapshot.stat().st_mtime
    ).isoformat(timespec="seconds")
    assert "warning" not in maintenance


def test_invalid_stale_days_keeps_maintenance_fields(maintenance_env, monkeypatch):
    main, _ = maintenance_env

    monkeypatch.setenv("ENGINE_BACKUP_STALE_DAYS", "abc")
    response = TestClient(main.app).get("/health/details")

    assert response.status_code == 200
    maintenance = response.json()["maintenance"]
    assert "backup_startup" in maintenance
    assert "usage_prune" in maintenance
    assert "error" not in maintenance
