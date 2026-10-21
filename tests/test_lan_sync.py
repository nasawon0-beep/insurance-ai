from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from lan_sync.store import AuthError, ConflictKind, SyncStore, ValidationError


class SyncStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "synthetic-sync.sqlite3"
        self.store = SyncStore(self.db)
        self.store.initialize()
        pair = self.store.create_pairing_code("test-pc", ttl_seconds=60)
        self.device = self.store.pair_device(pair.code, "android-test")
        self.auth = self.store.authenticate(self.device.token)
        self.store.seed_synthetic_customer(
            "c-1", {"name": "합성 고객", "phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def push(self, op_id, *, patch=None, base=None, deleted=False, entity="customer", entity_id="c-1"):
        return self.store.push(
            self.auth,
            [{
                "operation_id": op_id,
                "entity_type": entity,
                "entity_id": entity_id,
                "base": base or {},
                "patch": patch or {},
                "deleted": deleted,
            }],
        )[0]

    def test_pairing_is_one_time_and_revocation_blocks_access(self):
        code = self.store.create_pairing_code("test-pc", ttl_seconds=60)
        paired = self.store.pair_device(code.code, "phone-2")
        with self.assertRaises(AuthError):
            self.store.pair_device(code.code, "phone-3")
        self.store.revoke_device(paired.device_id)
        with self.assertRaises(AuthError):
            self.store.authenticate(paired.token)

    def test_unauthenticated_access_is_blocked(self):
        with self.assertRaises(AuthError):
            self.store.authenticate("not-a-token")

    def test_non_overlapping_fields_merge_without_clock_ordering(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        self.push("pc-address", patch={"address": "PC 주소"}, base=base)
        result = self.push("mobile-memo", patch={"memo": "모바일 메모"}, base=base)
        self.assertEqual("applied", result.status)
        entity = self.store.get_entity("customer", "c-1")
        self.assertEqual("PC 주소", entity["address"])
        self.assertEqual("모바일 메모", entity["memo"])

    def test_same_field_conflict_preserves_both_values_for_user_choice(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        self.push("pc-memo", patch={"memo": "PC 메모"}, base=base)
        result = self.push("mobile-memo", patch={"memo": "모바일 메모"}, base=base)
        self.assertEqual("conflict", result.status)
        conflict = self.store.list_conflicts(self.auth)[0]
        self.assertEqual(ConflictKind.FIELD.value, conflict["kind"])
        self.assertEqual("PC 메모", conflict["remote"]["memo"])
        self.assertEqual("모바일 메모", conflict["local"]["memo"])
        self.assertEqual("PC 메모", self.store.get_entity("customer", "c-1")["memo"])
        self.store.resolve_conflict(self.auth, conflict["id"], {"memo": "모바일 메모"})
        self.assertEqual("모바일 메모", self.store.get_entity("customer", "c-1")["memo"])

    def test_delete_modify_conflict_preserves_tombstone_and_modified_value(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        self.push("mobile-change", patch={"memo": "오프라인 수정"}, base=base)
        result = self.push("pc-delete", base=base, deleted=True)
        self.assertEqual("conflict", result.status)
        conflict = self.store.list_conflicts(self.auth)[0]
        self.assertEqual(ConflictKind.DELETE_MODIFY.value, conflict["kind"])
        self.assertTrue(conflict["local_deleted"])
        self.assertEqual("오프라인 수정", conflict["remote"]["memo"])

    def test_operation_replay_is_idempotent(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        first = self.push("same-op", patch={"phone": "010-1111-2222"}, base=base)
        cursor = self.store.current_cursor()
        second = self.push("same-op", patch={"phone": "SHOULD-NOT-APPLY"}, base=base)
        self.assertEqual(first, second)
        self.assertEqual(cursor, self.store.current_cursor())
        self.assertEqual("010-1111-2222", self.store.get_entity("customer", "c-1")["phone"])

    def test_partial_batch_failure_rolls_back_all_operations(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        with self.assertRaises(ValidationError):
            self.store.push(self.auth, [
                {"operation_id": "valid", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"memo": "변경"}, "deleted": False},
                {"operation_id": "invalid", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"name": "금지된 수정"}, "deleted": False},
            ])
        self.assertEqual("기준 메모", self.store.get_entity("customer", "c-1")["memo"])
        self.assertEqual(0, self.store.operation_count())

    def test_read_only_entities_and_customer_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.push("policy-write", entity="policy", entity_id="p-1", patch={"premium": 1})
        with self.assertRaises(ValidationError):
            self.push("name-write", patch={"name": "수정 금지"})

    def test_consultation_requires_existing_customer(self):
        with self.assertRaises(ValidationError):
            self.push("orphan", entity="consultation", entity_id="q-1", patch={"customer_id": "missing", "content": "합성"})

    def test_cursor_and_tombstone_ack_survive_database_restart(self):
        base = {"name": "합성 고객", "phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        self.push("delete", base=base, deleted=True)
        changes = self.store.pull(self.auth, cursor=0, limit=100)
        tombstones = [change for change in changes.items if change["deleted"]]
        self.assertEqual(1, len(tombstones))
        self.store.ack_tombstones(self.auth, [tombstones[0]["cursor"]])
        cursor = changes.next_cursor
        token = self.device.token
        self.store.close()
        self.store = SyncStore(self.db)
        self.store.initialize()
        auth = self.store.authenticate(token)
        self.assertEqual(cursor, self.store.current_cursor())
        self.assertEqual([], self.store.pending_tombstones(auth))

    def test_out_of_order_wall_clock_does_not_override_conflict_rules(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        first = self.store.push(self.auth, [{"operation_id": "future", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"memo": "첫 변경"}, "deleted": False, "client_time": "2099-01-01T00:00:00Z"}])[0]
        second = self.store.push(self.auth, [{"operation_id": "past", "entity_type": "customer", "entity_id": "c-1", "base": base, "patch": {"memo": "둘째 변경"}, "deleted": False, "client_time": "2000-01-01T00:00:00Z"}])[0]
        self.assertEqual("applied", first.status)
        self.assertEqual("conflict", second.status)

    def test_offline_outbox_reconnect_and_replay_reaches_same_state(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        durable_outbox = [{
            "operation_id": "offline-1", "entity_type": "customer", "entity_id": "c-1",
            "base": base, "patch": {"phone": "010-3333-4444"}, "deleted": False,
        }]
        # PC가 꺼진 동안 서버 호출 없이 outbox와 로컬 값이 유지된 상황을 모사한다.
        self.assertEqual(1, len(durable_outbox))
        first = self.store.push(self.auth, durable_outbox)[0]
        replay = self.store.push(self.auth, durable_outbox)[0]
        self.assertEqual(first, replay)
        self.assertEqual("010-3333-4444", self.store.get_entity("customer", "c-1")["phone"])
        self.assertEqual(1, self.store.operation_count())

    def test_delete_conflict_can_be_resolved_by_explicit_user_choice(self):
        base = {"phone": "010-0000-0000", "address": "기준 주소", "memo": "기준 메모"}
        self.push("mobile-change-2", patch={"memo": "보존할 수정"}, base=base)
        result = self.push("pc-delete-2", base=base, deleted=True)
        self.assertEqual("conflict", result.status)
        self.store.resolve_conflict(self.auth, result.conflict_id, {}, deleted=True)
        self.assertEqual({}, self.store.get_entity("customer", "c-1"))
        tombstones = [item for item in self.store.pull(self.auth, 0).items if item["deleted"]]
        self.assertEqual(1, len(tombstones))


if __name__ == "__main__":
    unittest.main()
