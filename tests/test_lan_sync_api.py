from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from fastapi.testclient import TestClient
from lan_sync.app import create_app


class LanSyncApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name) / "api.sqlite3", enable_production_bridge=False)
        self.store = self.app.state.sync_store
        self.store.seed_synthetic_customer("c-1", {"name": "합성 고객", "phone": "010-0000"})

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_cleartext_transport_is_rejected(self):
        with TestClient(self.app, base_url="http://testserver", raise_server_exceptions=False) as client:
            response = client.post("/v1/pair", json={"code": "000000", "device_name": "phone"})
        self.assertEqual(426, response.status_code)

    def test_unpaired_sync_is_rejected(self):
        with TestClient(self.app, base_url="https://testserver") as client:
            response = client.post("/v1/sync", json={"cursor": 0, "operations": []})
        self.assertEqual(401, response.status_code)

    def test_pair_once_then_authenticated_sync_and_revocation(self):
        pair = self.store.create_pairing_code("synthetic-pc", 60)
        with TestClient(self.app, base_url="https://testserver") as client:
            paired = client.post("/v1/pair", json={"code": pair.code, "device_name": "synthetic-phone"})
            self.assertEqual(200, paired.status_code)
            body = paired.json()
            duplicate = client.post("/v1/pair", json={"code": pair.code, "device_name": "other"})
            self.assertEqual(401, duplicate.status_code)
            headers = {"Authorization": f"Bearer {body['token']}"}
            sync = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            self.assertEqual(200, sync.status_code)
            self.store.revoke_device(body["device_id"])
            revoked = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            self.assertEqual(401, revoked.status_code)


if __name__ == "__main__":
    unittest.main()
