"""사용 로그 테스트 (database/usage.py + /usage, /usage/export)."""
import base64
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx")

_KEY = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def env(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "data" / "customers.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _KEY)
    monkeypatch.setenv("ENGINE_BACKUP", "0")
    monkeypatch.delenv("RRN_INPUT_ENABLED", raising=False)
    crypto.reset_cache()
    yield tmp_path
    crypto.reset_cache()


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def test_off_whitelist_event_rejected(client):
    r = client.post("/usage", json={"event": "steal_all_data", "props": {"x": 1}})
    assert r.status_code == 200 and r.json() == {"recorded": False}


def test_known_event_recorded(client):
    r = client.post("/usage", json={"event": "app_open", "device_id": "dev-1", "app_version": "0.1.0"})
    assert r.json() == {"recorded": True}


def test_prop_sanitizer_strips_disallowed_and_bad_types(client):
    from database import usage

    clean = usage.sanitize(
        "capture_run",
        {
            "source": "text",
            "items": 3,
            "customer_name": "홍길동",     # 허용 키 아님 → 제거
            "note": "free text here",       # 허용 키 아님 → 제거
        },
    )
    assert clean == {"source": "text", "items": 3}

    # 잘못된 열거값 / 타입
    assert usage.sanitize("capture_run", {"source": "carrier-pigeon", "items": "3"}) == {"items": 3}
    assert usage.sanitize("login", {"ok": "yes"}) == {}          # bool 아님 → 제거
    long = "x" * 200
    assert usage.sanitize("error", {"where": long, "status": 500}) == {"status": 500}


def test_usage_export_raw_and_daily(client):
    client.post("/usage", json={"event": "app_open"})
    client.post("/usage", json={"event": "login", "props": {"ok": True}})
    client.post("/usage", json={"event": "login", "props": {"ok": False}})

    raw = client.get("/usage/export", params={"mode": "raw"})
    assert raw.status_code == 200
    lines = raw.text.strip().splitlines()
    assert lines[0].lstrip("﻿") == "event,ts,props_json,device_id"
    assert len(lines) == 1 + 3

    daily = client.get("/usage/export", params={"mode": "daily"}).text.strip().splitlines()
    assert daily[0].lstrip("﻿") == "event,day,count"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert any(row == f"login,{today},2" for row in daily[1:])


def test_prune_180_days(client):
    from database import usage
    from database.db import connect, init_schema

    conn = connect()
    init_schema(conn)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    conn.execute(
        "INSERT INTO usage_log (id, event, props, device_id, app_version, created_at) "
        "VALUES ('old1','app_open',NULL,NULL,NULL,?)",
        (old,),
    )
    conn.commit()
    usage.record(conn, "app_open")
    assert conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0] == 2
    removed = usage.prune(conn, 180)
    assert removed == 1
    assert conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0] == 1
    conn.close()
