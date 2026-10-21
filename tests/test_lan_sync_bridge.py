from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

from database import crypto, db, policy_coverages, repo
from fastapi.testclient import TestClient
from lan_sync.app import create_app
from lan_sync.bridge import ProductionBridge
from lan_sync.store import ConflictKind, SyncStore


class ProductionBridgeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "synthetic-production.sqlite3"
        self.old_env = {key: os.environ.get(key) for key in ("CUSTOMER_DB_PATH", "CUSTOMER_DB_KEY_B64")}
        os.environ["CUSTOMER_DB_PATH"] = str(self.db_path)
        os.environ["CUSTOMER_DB_KEY_B64"] = base64.b64encode(b"b" * 32).decode("ascii")
        crypto.reset_cache()
        conn = db.connect(self.db_path)
        db.init_schema(conn)
        self.customer = repo.create_customer(conn, {
            "name": "합성 고객", "phone": "010-0000-0000", "birth_date": "1990-01-02",
            "address": "합성 주소", "memo": "기준 메모", "occupation": "테스터",
        })
        self.policy = repo.create_policy(conn, self.customer["id"], {
            "insurer": "합성보험", "product_name": "합성 상품", "premium": 12345,
            "policy_number": "SYNTHETIC-ONLY", "insured_period": "100세만기",
        })
        self.consultation = repo.create_consultation(conn, self.customer["id"], {
            "consulted_at": "2026-10-21T00:00:00+00:00", "title": "합성 보장분석",
            "channel": "테스트", "content": "합성 상담 내용",
            "coverage_json": json.dumps([{"name": "합성 담보", "current": 1000}], ensure_ascii=False),
        })
        policy_coverages.create_policy_coverage(conn, self.customer["id"], {
            "policy_id": self.policy["id"], "standard_name": "합성 담보",
            "rider_name": "합성 특약", "amount": 1000,
        })
        conn.close()

        self.store = SyncStore(self.db_path)
        self.store.initialize()
        self.bridge = ProductionBridge(self.store)
        self.bridge.initialize()
        self.bridge.refresh()
        code = self.store.create_pairing_code("synthetic-pc", 60)
        paired = self.store.pair_device(code.code, "synthetic-android")
        self.auth = self.store.authenticate(paired.token)

    def tearDown(self):
        self.store.close()
        crypto.reset_cache()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def push(self, operation):
        return self.store.push(
            self.auth,
            [operation],
            apply_callback=self.bridge.apply_operation,
            refresh_callback=self.bridge.refresh,
        )[0]

    def customer_snapshot(self):
        return self.store.get_entity("customer", self.customer["id"])

    def test_pc_schema_supplies_customer_policy_consultation_coverages_and_analysis(self):
        pulled = self.store.pull(self.auth, 0, 100)
        kinds = {item["entity_type"] for item in pulled.items}
        self.assertTrue({"customer", "policy", "consultation", "policy_coverage", "coverage_analysis"} <= kinds)
        customer = self.customer_snapshot()
        self.assertEqual("합성 고객", customer["name"])
        self.assertNotIn("rrn", customer)
        analysis = next(item for item in pulled.items if item["entity_type"] == "coverage_analysis")
        self.assertEqual([{"name": "합성 담보", "current": 1000}], analysis["payload"]["summary"])

    def test_offline_customer_patch_persists_to_encrypted_canonical_db_without_losing_fields(self):
        base = self.customer_snapshot()
        result = self.push({
            "operation_id": "mobile-customer-1", "entity_type": "customer",
            "entity_id": self.customer["id"], "base": base,
            "patch": {"phone": "010-9999-9999", "memo": "모바일 메모"}, "deleted": False,
        })
        self.assertEqual("applied", result.status)
        conn = db.connect(self.db_path)
        stored = repo.get_customer(conn, self.customer["id"])
        raw = conn.execute("SELECT phone,memo FROM customers WHERE id=?", (self.customer["id"],)).fetchone()
        conn.close()
        self.assertEqual("010-9999-9999", stored["phone"])
        self.assertEqual("모바일 메모", stored["memo"])
        self.assertEqual("합성 고객", stored["name"])
        self.assertEqual("1990-01-02", stored["birth_date"])
        self.assertTrue(raw["phone"].startswith("enc:v1:"))
        self.assertTrue(raw["memo"].startswith("enc:v1:"))

    def test_mobile_consultation_is_idempotent_and_keeps_reference_integrity(self):
        operation = {
            "operation_id": "mobile-consultation-1", "entity_type": "consultation",
            "entity_id": "mobile-consultation-id", "base": {},
            "patch": {"customer_id": self.customer["id"], "consulted_at": "2026-10-21T01:00:00Z",
                      "title": "오프라인 상담", "channel": "방문", "content": "합성 신규 상담",
                      "follow_up_at": None, "follow_up_done_at": None},
            "deleted": False,
        }
        first = self.push(operation)
        replay = self.push(operation)
        self.assertEqual(first, replay)
        conn = db.connect(self.db_path)
        rows = conn.execute("SELECT COUNT(*) FROM consultations WHERE id='mobile-consultation-id'").fetchone()[0]
        saved = repo.get_consultation(conn, "mobile-consultation-id")
        conn.close()
        self.assertEqual(1, rows)
        self.assertEqual(self.customer["id"], saved["customer_id"])
        self.assertEqual("합성 신규 상담", saved["content"])

    def test_pc_and_mobile_non_overlapping_changes_merge_but_same_field_conflicts(self):
        base = self.customer_snapshot()
        conn = db.connect(self.db_path)
        repo.update_customer(conn, self.customer["id"], {"address": "PC 변경 주소"})
        conn.close()
        self.bridge.refresh()
        merged = self.push({
            "operation_id": "merge-1", "entity_type": "customer", "entity_id": self.customer["id"],
            "base": base, "patch": {"memo": "모바일 변경"}, "deleted": False,
        })
        self.assertEqual("applied", merged.status)
        conn = db.connect(self.db_path)
        after = repo.get_customer(conn, self.customer["id"])
        repo.update_customer(conn, self.customer["id"], {"phone": "010-1111-1111"})
        conn.close()
        self.assertEqual("PC 변경 주소", after["address"])
        self.assertEqual("모바일 변경", after["memo"])
        same_base = self.customer_snapshot()
        self.bridge.refresh()
        conflict = self.push({
            "operation_id": "conflict-1", "entity_type": "customer", "entity_id": self.customer["id"],
            "base": same_base, "patch": {"phone": "010-2222-2222"}, "deleted": False,
        })
        self.assertEqual("conflict", conflict.status)
        item = self.store.list_conflicts(self.auth)[0]
        self.assertEqual(ConflictKind.FIELD.value, item["kind"])
        self.assertEqual("010-1111-1111", item["remote"]["phone"])
        self.assertEqual("010-2222-2222", item["local"]["phone"])

    def test_pc_delete_becomes_tombstone_and_old_mobile_update_is_preserved_as_conflict(self):
        base = self.customer_snapshot()
        conn = db.connect(self.db_path)
        self.assertTrue(repo.delete_customer(conn, self.customer["id"]))
        conn.close()
        self.bridge.refresh()
        tombstones = [item for item in self.store.pull(self.auth, 0, 200).items
                      if item["entity_type"] == "customer" and item["entity_id"] == self.customer["id"] and item["deleted"]]
        self.assertEqual(1, len(tombstones))
        result = self.push({
            "operation_id": "delete-conflict-1", "entity_type": "customer", "entity_id": self.customer["id"],
            "base": base, "patch": {"memo": "오프라인 수정 보존"}, "deleted": False,
        })
        self.assertEqual("conflict", result.status)
        conflict = self.store.list_conflicts(self.auth)[0]
        self.assertEqual(ConflictKind.DELETE_MODIFY.value, conflict["kind"])
        self.assertEqual("오프라인 수정 보존", conflict["local"]["memo"])
        self.assertTrue(conflict["remote_deleted"])

    def test_failure_after_canonical_write_rolls_back_both_canonical_and_sync_state_then_restart_retries(self):
        base = self.customer_snapshot()
        operation = {
            "operation_id": "fault-1", "entity_type": "customer", "entity_id": self.customer["id"],
            "base": base, "patch": {"phone": "010-7777-7777"}, "deleted": False,
        }
        self.bridge.fault_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("injected")) if stage == "after_canonical_write" else None
        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.push(operation)
        conn = db.connect(self.db_path)
        self.assertEqual("010-0000-0000", repo.get_customer(conn, self.customer["id"])["phone"])
        conn.close()
        self.assertEqual(base["phone"], self.customer_snapshot()["phone"])
        self.assertEqual(0, self.store.operation_count())

        self.store.close()
        self.store = SyncStore(self.db_path)
        self.store.initialize()
        self.bridge = ProductionBridge(self.store)
        self.bridge.initialize()
        self.bridge.refresh()
        self.bridge.fault_hook = None
        result = self.push(operation)
        self.assertEqual("applied", result.status)
        conn = db.connect(self.db_path)
        self.assertEqual("010-7777-7777", repo.get_customer(conn, self.customer["id"])["phone"])
        conn.close()

    def test_https_protocol_simulates_pc_to_android_offline_outbox_to_pc_and_readback(self):
        self.store.close()
        app = create_app(self.db_path)
        self.store = app.state.sync_store
        code = self.store.create_pairing_code("synthetic-pc", 60)
        with TestClient(app, base_url="https://synthetic-pc") as client:
            paired = client.post("/v1/pair", json={"code": code.code, "device_name": "synthetic-android"})
            self.assertEqual(200, paired.status_code)
            headers = {"Authorization": f"Bearer {paired.json()['token']}"}
            initial = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            self.assertEqual(200, initial.status_code)
            customer_change = next(
                item for item in initial.json()["changes"]
                if item["entity_type"] == "customer" and item["entity_id"] == self.customer["id"]
            )
            offline_operation = {
                "operation_id": "android-outbox-roundtrip", "entity_type": "customer",
                "entity_id": self.customer["id"], "base": customer_change["payload"],
                "patch": {"address": "오프라인 합성 주소"}, "deleted": False,
            }
            reconnected = client.post(
                "/v1/sync", headers=headers,
                json={"cursor": initial.json()["next_cursor"], "operations": [offline_operation]},
            )
            self.assertEqual(200, reconnected.status_code)
            self.assertEqual("applied", reconnected.json()["results"][0]["status"])
            readback = client.post("/v1/sync", headers=headers, json={"cursor": 0, "operations": []})
            same = next(
                item for item in readback.json()["changes"]
                if item["entity_type"] == "customer" and item["entity_id"] == self.customer["id"]
                and item["payload"].get("address") == "오프라인 합성 주소"
            )
            self.assertEqual("오프라인 합성 주소", same["payload"]["address"])
        conn = db.connect(self.db_path)
        self.assertEqual("오프라인 합성 주소", repo.get_customer(conn, self.customer["id"])["address"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
