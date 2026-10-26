from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

from lan_sync.admin import tls_rollover_ready
from lan_sync.app import create_app
from lan_sync.certificate import ensure_certificate


def test_synthetic_address_a_to_b_preserves_tls_identity_device_and_operation(tmp_path):
    instance_id = "0123456789abcdef0123456789abcdef"
    database = tmp_path / "synthetic.sqlite3"
    tls_dir = tmp_path / "tls"
    first = ensure_certificate(tls_dir, ["192.168.50.10"], valid_days=30, renew_before_days=5)

    app_a = create_app(
        database,
        enable_production_bridge=False,
        pc_instance_id=instance_id,
        tls_identity_spki_sha256=first["spki_sha256"],
    )
    store_a = app_a.state.sync_store
    store_a.seed_synthetic_customer("c-1", {"name": "합성 고객", "phone": "010-0000"})
    code = store_a.create_pairing_code("합성 PC", 60)
    with TestClient(app_a, base_url="https://192.168.50.10") as client:
        paired = client.post("/v1/pair", json={"code": code.code, "device_name": "합성 폰"}).json()
        headers = {"Authorization": f"Bearer {paired['token']}"}
        bound = client.post(
            "/v1/tls-identity/bind",
            headers=headers,
            json={"spki_sha256": first["spki_sha256"]},
        )
        assert bound.status_code == 200
        operation = {
            "operation_id": "offline-address-change-1",
            "entity_type": "customer",
            "entity_id": "c-1",
            "base": {"name": "합성 고객", "phone": "010-0000"},
            "patch": {"memo": "오프라인 보존"},
            "deleted": False,
        }
        first_sync = client.post(
            "/v1/sync", headers=headers, json={"cursor": 0, "operations": [operation]}
        )
        assert first_sync.status_code == 200
        assert store_a.operation_count() == 1

    assert tls_rollover_ready(database, Path(first["cert_path"])) is True
    rotated = ensure_certificate(
        tls_dir,
        ["192.168.50.11"],
        valid_days=30,
        renew_before_days=5,
        allow_address_reissue=True,
    )
    assert rotated["spki_sha256"] == first["spki_sha256"]
    assert rotated["fingerprint"] != first["fingerprint"]

    app_b = create_app(
        database,
        enable_production_bridge=False,
        pc_instance_id=instance_id,
        tls_identity_spki_sha256=rotated["spki_sha256"],
    )
    store_b = app_b.state.sync_store
    with TestClient(app_b, base_url="https://192.168.50.11") as client:
        continuity = client.post("/v1/continuity", headers=headers, json={})
        assert continuity.status_code == 200
        assert continuity.json()["device_id"] == paired["device_id"]
        assert continuity.json()["pc_instance_id"] == instance_id
        assert continuity.json()["tls_identity_spki_sha256"] == first["spki_sha256"]

        replay = client.post(
            "/v1/sync", headers=headers, json={"cursor": 0, "operations": [operation]}
        )
        assert replay.status_code == 200
        assert replay.json()["results"] == first_sync.json()["results"]
        assert store_b.operation_count() == 1

        store_b.revoke_device(paired["device_id"])
        assert client.post("/v1/continuity", headers=headers, json={}).status_code == 401

    spoof = ensure_certificate(tmp_path / "spoof", ["192.168.50.11"], valid_days=30, renew_before_days=5)
    assert spoof["spki_sha256"] != rotated["spki_sha256"]
