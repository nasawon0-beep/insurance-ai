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
        self.instance_id = "0123456789abcdef0123456789abcdef"
        self.app = create_app(Path(self.tmp.name) / "api.sqlite3", enable_production_bridge=False, pc_instance_id=self.instance_id)
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
            self.assertEqual(self.instance_id, body["pc_instance_id"])
            duplicate = client.post("/v1/pair", json={"code": pair.code, "device_name": "other"})
            self.assertEqual(401, duplicate.status_code)
            headers = {"Authorization": f"Bearer {body['token']}"}
            sync = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            self.assertEqual(200, sync.status_code)
            self.store.revoke_device(body["device_id"])
            revoked = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            self.assertEqual(401, revoked.status_code)

    def test_recovery_response_loss_retry_preserves_identity_ownership_and_idempotency(self):
        pair = self.store.create_pairing_code("synthetic-pc", 60)
        with TestClient(self.app, base_url="https://testserver") as client:
            paired = client.post("/v1/pair", json={"code": pair.code, "device_name": "synthetic-phone"}).json()
            old_headers = {"Authorization": f"Bearer {paired['token']}"}
            operation = {"operation_id": "before-revoke", "entity_type": "customer", "entity_id": "c-1", "base": {"name": "합성 고객", "phone": "010-0000"}, "patch": {"memo": "보존"}, "deleted": False}
            first = client.post("/v1/sync", headers=old_headers, json={"cursor": 0, "operations": [operation]})
            self.assertEqual(200, first.status_code)
            self.store.revoke_device(paired["device_id"])
            recovery = self.store.create_recovery_code(paired["device_id"], self.instance_id, 60)
            payload = {
                "code": recovery.code,
                "candidate_token": "replacement-token-with-sufficient-entropy-api-0001",
                "expected_device_id": paired["device_id"],
                "pc_instance_id": self.instance_id,
            }
            accepted_but_response_lost = client.post("/v1/recover", json=payload)
            retried = client.post("/v1/recover", json=payload)
            self.assertEqual(200, accepted_but_response_lost.status_code)
            self.assertEqual(accepted_but_response_lost.json(), retried.json())
            self.assertEqual(paired["device_id"], retried.json()["device_id"])
            new_headers = {"Authorization": "Bearer replacement-token-with-sufficient-entropy-api-0001"}
            replay = client.post("/v1/sync", headers=new_headers, json={"cursor": 0, "operations": [operation]})
            self.assertEqual(first.json()["results"], replay.json()["results"])
            self.assertEqual(1, self.store.operation_count())
            self.assertEqual(401, client.post("/v1/sync", headers=old_headers, json={"cursor": 0, "operations": []}).status_code)

    def test_android_resolve_contract_clears_conflict_after_server_confirmation_and_refresh(self):
        pair = self.store.create_pairing_code("synthetic-pc", 60)
        base = {"name": "합성 고객", "phone": "010-0000"}
        with TestClient(self.app, base_url="https://testserver") as client:
            paired = client.post("/v1/pair", json={"code": pair.code, "device_name": "synthetic-phone"})
            self.assertEqual(200, paired.status_code)
            headers = {"Authorization": f"Bearer {paired.json()['token']}"}

            pc_change = client.post("/v1/sync", headers=headers, json={
                "cursor": 0,
                "operations": [{
                    "operation_id": "pc-change",
                    "entity_type": "customer",
                    "entity_id": "c-1",
                    "base": base,
                    "patch": {"memo": "PC"},
                    "deleted": False,
                }],
            })
            self.assertEqual(200, pc_change.status_code)

            mobile_change = client.post("/v1/sync", headers=headers, json={
                "cursor": pc_change.json()["next_cursor"],
                "operations": [{
                    "operation_id": "mobile-change",
                    "entity_type": "customer",
                    "entity_id": "c-1",
                    "base": base,
                    "patch": {"memo": "모바일"},
                    "deleted": False,
                }],
            })
            self.assertEqual(200, mobile_change.status_code)
            conflicts = mobile_change.json()["conflicts"]
            self.assertEqual(1, len(conflicts))

            resolved = client.post(
                f"/v1/conflicts/{conflicts[0]['id']}/resolve",
                headers=headers,
                json={"resolution": {"memo": "모바일"}, "deleted": False},
            )
            self.assertEqual(200, resolved.status_code)
            resolution = resolved.json()
            self.assertIsInstance(resolution["cursor"], int)
            self.assertEqual("resolved", resolution["status"])
            self.assertEqual(conflicts[0]["id"], resolution["conflict_id"])
            self.assertEqual("customer", resolution["entity_type"])
            self.assertEqual("c-1", resolution["entity_id"])
            self.assertEqual("모바일", resolution["payload"]["memo"])
            self.assertFalse(resolution["deleted"])

            refreshed = client.post("/v1/sync", headers=headers, json={
                "cursor": mobile_change.json()["next_cursor"],
                "operations": [],
            })
            self.assertEqual(200, refreshed.status_code)
            self.assertEqual([], refreshed.json()["conflicts"])
            self.assertEqual("모바일", self.store.get_entity("customer", "c-1")["memo"])

    def test_continuity_proves_same_device_and_preserves_conflict_ownership(self):
        first_code = self.store.create_pairing_code("synthetic-pc", 60)
        second_code = self.store.create_pairing_code("synthetic-pc", 60)
        base = {"name": "합성 고객", "phone": "010-0000"}
        with TestClient(self.app, base_url="https://address-a") as client:
            first = client.post("/v1/pair", json={"code": first_code.code, "device_name": "original-phone"}).json()
            first_headers = {"Authorization": f"Bearer {first['token']}"}
            pc_change = client.post("/v1/sync", headers=first_headers, json={
                "cursor": 0,
                "operations": [{"operation_id": "pc-a", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"memo": "PC"}, "deleted": False}],
            }).json()
            conflict_response = client.post("/v1/sync", headers=first_headers, json={
                "cursor": pc_change["next_cursor"],
                "operations": [{"operation_id": "mobile-a", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"memo": "충골-모바일-b"}, "deleted": False}],
            }).json()
            conflict_id = conflict_response["conflicts"][0]["id"]

            second = client.post("/v1/pair", json={"code": second_code.code, "device_name": "different-phone"}).json()
            second_headers = {"Authorization": f"Bearer {second['token']}"}
            original = client.post("/v1/continuity", headers=first_headers, json={})
            different = client.post("/v1/continuity", headers=second_headers, json={})
            wrong_owner_resolve = client.post(
                f"/v1/conflicts/{conflict_id}/resolve",
                headers=second_headers,
                json={"resolution": {"memo": "충골-모바일-b"}, "deleted": False},
            )
            right_owner_resolve = client.post(
                f"/v1/conflicts/{conflict_id}/resolve",
                headers=first_headers,
                json={"resolution": {"memo": "충골-모바일-b"}, "deleted": False},
            )
            idempotent_retry = client.post(
                f"/v1/conflicts/{conflict_id}/resolve",
                headers=first_headers,
                json={"resolution": {"memo": "충골-모바일-b"}, "deleted": False},
            )
            changed_retry = client.post(
                f"/v1/conflicts/{conflict_id}/resolve",
                headers=first_headers,
                json={"resolution": {"memo": "임의교정금지"}, "deleted": False},
            )

        self.assertEqual(200, original.status_code)
        self.assertEqual(first["device_id"], original.json()["device_id"])
        self.assertEqual(self.instance_id, original.json()["pc_instance_id"])
        self.assertIn(conflict_id, original.json()["unresolved_conflict_ids"])
        self.assertEqual(200, different.status_code)
        self.assertEqual(second["device_id"], different.json()["device_id"])
        self.assertNotIn(conflict_id, different.json()["unresolved_conflict_ids"])
        self.assertEqual(422, wrong_owner_resolve.status_code)
        self.assertEqual(200, right_owner_resolve.status_code)
        self.assertEqual("충골-모바일-b", right_owner_resolve.json()["payload"]["memo"])
        self.assertEqual(200, idempotent_retry.status_code)
        self.assertEqual(right_owner_resolve.json()["cursor"], idempotent_retry.json()["cursor"])
        self.assertEqual(422, changed_retry.status_code)

    def test_pairing_limit_returns_429_ignores_forwarded_header_and_does_not_affect_sync(self):
        pair = self.store.create_pairing_code("synthetic-pc", 60)
        with TestClient(self.app, base_url="https://testserver") as client:
            paired = client.post("/v1/pair", json={"code": pair.code, "device_name": "synthetic-phone"})
            self.assertEqual(200, paired.status_code)
            token = paired.json()["token"]
            for number in range(5):
                rejected = client.post(
                    "/v1/pair",
                    headers={"X-Forwarded-For": f"198.51.100.{number}"},
                    json={"code": "000000", "device_name": "attacker"},
                )
                self.assertEqual(401, rejected.status_code)

            limited = client.post(
                "/v1/pair",
                headers={"X-Forwarded-For": "203.0.113.99"},
                json={"code": "000000", "device_name": "attacker"},
            )
            self.assertEqual(429, limited.status_code)
            self.assertEqual("30", limited.headers["Retry-After"])
            self.assertEqual({"detail": "pairing temporarily unavailable"}, limited.json())

            sync = client.post(
                "/v1/sync",
                headers={"Authorization": f"Bearer {token}"},
                json={"cursor": 0, "operations": []},
            )
            self.assertEqual(200, sync.status_code)


if __name__ == "__main__":
    unittest.main()
