"""Bridge between the LAN sync log and the production customer repository.

The sync tables live in the same SQLite file as the canonical tables.  Mobile writes
and sync operation receipts therefore commit in one SQLite transaction.  Existing
repository writers are observed by refreshing decrypted, privacy-filtered snapshots
before each sync request; the existing loopback engine does not need to bind to LAN.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from database import family, policy_coverages, repo

from .store import SyncStore, ValidationError


_MANAGED_TYPES = frozenset({"customer", "policy", "consultation", "policy_coverage", "coverage_analysis", "family_relationship"})
_CUSTOMER_FIELDS = (
    "id", "name", "phone", "birth_date", "gender", "email", "address", "occupation",
    "tags", "memo", "customer_status", "birth_date_estimated", "first_registered_ym",
    "created_at", "updated_at",
)
_POLICY_FIELDS = (
    "id", "customer_id", "insurer", "product_name", "policy_number", "plan_type",
    "premium", "payment_cycle", "start_date", "end_date", "status", "document_status",
    "source_hash", "source_document_type", "memo", "document_id", "insured_period",
    "payment_period", "payment_end_date", "end_date_derived", "is_own",
    "policyholder_name", "policyholder_rel", "created_at", "updated_at",
)
_CONSULTATION_FIELDS = (
    "id", "customer_id", "consulted_at", "channel", "title", "content",
    "follow_up_at", "follow_up_done_at", "created_at", "updated_at",
)


class ProductionBridge:
    def __init__(self, store: SyncStore):
        self.store = store
        self.conn = store.conn
        self.fault_hook: Callable[[str], None] | None = None

    def initialize(self) -> None:
        tables = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"customers", "policies", "consultations", "policy_coverages", "family_relationships", "audit_log"}
        missing = required - tables
        if missing:
            raise ValidationError(f"production customer schema required; missing tables: {sorted(missing)}")

    @staticmethod
    def _select(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        return {field: source.get(field) for field in fields}

    def refresh(self) -> None:
        snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        customer_ids = [row[0] for row in self.conn.execute("SELECT id FROM customers ORDER BY id")]
        for customer_id in customer_ids:
            customer = repo.get_customer(self.conn, customer_id)
            if customer is None:
                continue
            snapshots[("customer", customer_id)] = self._select(customer, _CUSTOMER_FIELDS)

            for policy in repo.list_policies(self.conn, customer_id):
                snapshots[("policy", policy["id"])] = self._select(policy, _POLICY_FIELDS)

            for consultation in repo.list_consultations(self.conn, customer_id):
                snapshots[("consultation", consultation["id"])] = self._select(
                    consultation, _CONSULTATION_FIELDS
                )
                coverage_json = consultation.get("coverage_json")
                if coverage_json:
                    try:
                        summary = json.loads(coverage_json)
                    except (TypeError, json.JSONDecodeError):
                        summary = {"unparsed": True, "text": str(coverage_json)}
                    analysis_id = f"consultation:{consultation['id']}"
                    snapshots[("coverage_analysis", analysis_id)] = {
                        "customer_id": customer_id,
                        "generated_at": consultation.get("consulted_at") or consultation.get("created_at"),
                        "summary": summary,
                        "source_consultation_id": consultation["id"],
                        "source_hash": None,
                        "updated_at": consultation.get("updated_at"),
                    }

            for coverage in policy_coverages.list_policy_coverages(self.conn, customer_id):
                snapshots[("policy_coverage", coverage["id"])] = dict(coverage)

        for relationship in family.list_sync_snapshots(self.conn):
            snapshots[("family_relationship", relationship["id"])] = relationship

        self.store.reconcile_snapshots(snapshots, _MANAGED_TYPES)

    def apply_operation(self, operation: dict[str, Any], merged: dict[str, Any], deleted: bool) -> None:
        entity_type = operation["entity_type"]
        entity_id = operation["entity_id"]
        if deleted:
            exists = self.conn.execute(
                f"SELECT 1 FROM {'customers' if entity_type == 'customer' else 'consultations'} WHERE id=?",
                (entity_id,),
            ).fetchone()
            if exists:
                raise ValidationError("mobile deletion is not enabled; PC deletion is propagated as a tombstone")
            return
        if entity_type == "customer":
            self._apply_customer(entity_id, operation["patch"], operation)
        elif entity_type == "consultation":
            self._apply_consultation(entity_id, merged, operation)
        else:
            raise ValidationError(f"mobile write is not supported for {entity_type}")
        if self.fault_hook:
            self.fault_hook("after_canonical_write")

    def _audit(self, *, action: str, entity: str, entity_id: str, customer_id: str, fields: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO audit_log(id,actor,action,entity,entity_id,customer_id,fields,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (repo._new_id(), "mobile-sync", action, entity, entity_id, customer_id,
             json.dumps(sorted(fields), ensure_ascii=False), repo._now()),
        )

    def _apply_customer(self, customer_id: str, patch: dict[str, Any], operation: dict[str, Any]) -> None:
        allowed = {key: value for key, value in patch.items() if key in {"phone", "address", "memo"}}
        if not allowed:
            raise ValidationError("customer patch has no writable fields")
        encrypted = {key: repo._encrypt(value, key, repo._CUSTOMER_ENC) for key, value in allowed.items()}
        encrypted["updated_at"] = repo._now()
        assignments = ", ".join(f"{key}=?" for key in encrypted)
        updated = self.conn.execute(
            f"UPDATE customers SET {assignments} WHERE id=?",
            [*encrypted.values(), customer_id],
        ).rowcount
        if updated != 1:
            raise ValidationError("customer no longer exists in canonical database")
        self._audit(
            action="mobile_sync_patch", entity="customer", entity_id=customer_id,
            customer_id=customer_id, fields=list(allowed),
        )

    def _apply_consultation(self, consultation_id: str, payload: dict[str, Any], operation: dict[str, Any]) -> None:
        customer_id = payload.get("customer_id")
        if not customer_id or not self.conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
            raise ValidationError("consultation customer no longer exists in canonical database")
        if self.conn.execute("SELECT 1 FROM consultations WHERE id=?", (consultation_id,)).fetchone():
            existing = repo.get_consultation(self.conn, consultation_id)
            comparable = {field: existing.get(field) for field in repo._CONSULT_FIELDS if field != "transcript"}
            requested = {field: payload.get(field) for field in repo._CONSULT_FIELDS if field != "transcript"}
            if comparable != requested:
                raise ValidationError("consultation id already exists with different content")
            return
        now = repo._now()
        values = {column: None for column in repo._CONSULT_COLS}
        values.update({
            "id": consultation_id,
            "customer_id": customer_id,
            "created_at": now,
            "updated_at": now,
        })
        for field in repo._CONSULT_FIELDS:
            if field == "transcript":
                continue
            value = payload.get(field)
            values[field] = repo._encrypt(value, field, repo._CONSULT_ENC)
        values["consulted_at"] = payload.get("consulted_at") or now
        self.conn.execute(
            f"INSERT INTO consultations ({', '.join(repo._CONSULT_COLS)}) VALUES ({', '.join('?' for _ in repo._CONSULT_COLS)})",
            [values[column] for column in repo._CONSULT_COLS],
        )
        self._audit(
            action="mobile_sync_create", entity="consultation", entity_id=consultation_id,
            customer_id=customer_id, fields=[key for key in operation["patch"] if key != "customer_id"],
        )
