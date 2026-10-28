from __future__ import annotations

import base64

from fastapi.testclient import TestClient


def setup_family_sync(tmp_path, monkeypatch):
    from database import crypto, db, family, repo

    path = tmp_path / "family-sync.sqlite3"
    monkeypatch.setenv(
        "CUSTOMER_DB_KEY_B64",
        base64.b64encode(b"s" * 32).decode("ascii"),
    )
    crypto.reset_cache()
    conn = db.connect(path)
    db.init_schema(conn)
    a = repo.create_customer(conn, {"name": "A합성고객"})
    b = repo.create_customer(conn, {"name": "B합성고객"})
    edge = family.create_relationships(conn, a["id"], b["id"], "SPOUSE")[0]
    conn.close()
    return path, a, b, edge


def pair(app, client):
    code = app.state.sync_store.create_pairing_code("family-pc", 60)
    response = client.post(
        "/v1/pair",
        json={"code": code.code, "device_name": "family-mobile"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_family_sync_is_capability_gated_for_old_clients(tmp_path, monkeypatch):
    from lan_sync.app import create_app

    path, _a, _b, edge = setup_family_sync(tmp_path, monkeypatch)
    app = create_app(path)
    with TestClient(app, base_url="https://synthetic.local") as client:
        token = pair(app, client)
        headers = {"Authorization": f"Bearer {token}"}
        old = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
        assert old.status_code == 200
        assert old.json()["schema_version"] == 1
        assert "family_relationship.v1" in old.json()["available_capabilities"]
        assert all(item["entity_type"] != "family_relationship" for item in old.json()["changes"])

        current = client.post(
            "/v1/sync",
            headers=headers,
            json={"cursor": 0, "schema_version": 2, "capabilities": ["family_relationship.v1"], "operations": []},
        )
        assert current.status_code == 200, current.text
        assert current.json()["schema_version"] == 2
        family_changes = [
            item for item in current.json()["changes"]
            if item["entity_type"] == "family_relationship"
        ]
        assert len(family_changes) == 1
        assert family_changes[0]["entity_id"] == edge["id"]
        assert family_changes[0]["payload"]["relation_a_to_b"] == "SPOUSE"
        assert family_changes[0]["deleted"] is False


def test_family_sync_tombstone_and_authentication(tmp_path, monkeypatch):
    from database import db, family
    from lan_sync.app import create_app

    path, a, _b, edge = setup_family_sync(tmp_path, monkeypatch)
    app = create_app(path)
    body = {"cursor": 0, "schema_version": 2, "capabilities": ["family_relationship.v1"], "operations": []}
    with TestClient(app, base_url="https://synthetic.local") as client:
        token = pair(app, client)
        headers = {"Authorization": f"Bearer {token}"}
        unauthorized = client.post("/v1/sync", json=body)
        assert unauthorized.status_code == 401
        first = client.post("/v1/sync", headers=headers, json=body).json()
        cursor = first["next_cursor"]

        conn = db.connect(path)
        assert family.delete_relationship(conn, a["id"], edge["id"])
        conn.close()

        deleted = client.post(
            "/v1/sync",
            headers=headers,
            json={**body, "cursor": cursor},
        )
        assert deleted.status_code == 200, deleted.text
        tombstones = [
            item for item in deleted.json()["changes"]
            if item["entity_type"] == "family_relationship" and item["entity_id"] == edge["id"]
        ]
        assert len(tombstones) == 1
        assert tombstones[0]["deleted"] is True
