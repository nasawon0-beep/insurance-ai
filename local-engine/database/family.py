"""Canonical customer-family relationship storage.

One row owns one unordered customer pair. ``relation_a_to_b`` describes customer B
from customer A's perspective; reverse reads are derived, never duplicated.  Compound
spouse/child operations use one ``BEGIN IMMEDIATE`` transaction so partial family
links cannot escape after validation or write failure.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from . import repo
from .crypto import get_cipher

RELATIONSHIPS = frozenset({"SPOUSE", "PARENT", "CHILD", "SIBLING", "OTHER"})
RELATIONSHIP_LABELS = {
    "SPOUSE": "배우자",
    "PARENT": "부모",
    "CHILD": "자녀",
    "SIBLING": "형제자매",
    "OTHER": "기타",
}
_INVERSE = {
    "SPOUSE": "SPOUSE",
    "PARENT": "CHILD",
    "CHILD": "PARENT",
    "SIBLING": "SIBLING",
    "OTHER": "OTHER",
}
_BATCH_SCOPE = "family_relationship_batch"


class FamilyError(RuntimeError):
    pass


class FamilyNotFound(FamilyError):
    pass


class FamilyConflict(FamilyError):
    pass


class FamilyValidation(FamilyError):
    pass


def inverse(relationship: str) -> str:
    try:
        return _INVERSE[relationship]
    except KeyError as exc:
        raise FamilyValidation("지원하지 않는 가족 관계입니다.") from exc


def _normalize(source_id: str, target_id: str, relationship: str) -> tuple[str, str, str]:
    if source_id == target_id:
        raise FamilyValidation("자기 자신은 가족으로 연결할 수 없습니다.")
    if relationship not in RELATIONSHIPS:
        raise FamilyValidation("지원하지 않는 가족 관계입니다.")
    if source_id < target_id:
        return source_id, target_id, relationship
    return target_id, source_id, inverse(relationship)


def _customer_exists(conn: sqlite3.Connection, customer_id: str) -> bool:
    return conn.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone() is not None


def _relation_for(row: sqlite3.Row | dict[str, Any], customer_id: str) -> str:
    return row["relation_a_to_b"] if row["customer_a_id"] == customer_id else inverse(row["relation_a_to_b"])


def _edge_between(conn: sqlite3.Connection, first_id: str, second_id: str):
    a, b = sorted((first_id, second_id))
    return conn.execute(
        "SELECT * FROM family_relationships WHERE customer_a_id=? AND customer_b_id=?",
        (a, b),
    ).fetchone()


def _parent_child_for(source_id: str, target_id: str, relationship: str) -> tuple[str, str] | None:
    # ``relationship`` describes target from source's perspective.
    if relationship == "CHILD":
        return source_id, target_id
    if relationship == "PARENT":
        return target_id, source_id
    return None


def _parent_graph(conn: sqlite3.Connection) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT customer_a_id,customer_b_id,relation_a_to_b FROM family_relationships "
        "WHERE relation_a_to_b IN ('PARENT','CHILD')"
    ).fetchall():
        if row["relation_a_to_b"] == "CHILD":
            parent, child = row["customer_a_id"], row["customer_b_id"]
        else:
            parent, child = row["customer_b_id"], row["customer_a_id"]
        graph.setdefault(parent, set()).add(child)
    return graph


def _ensure_no_parent_cycle(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    relationship: str,
    pending: Iterable[tuple[str, str, str]] = (),
) -> None:
    pair = _parent_child_for(source_id, target_id, relationship)
    if pair is None:
        return
    parent, child = pair
    graph = _parent_graph(conn)
    for p_source, p_target, p_relation in pending:
        pending_pair = _parent_child_for(p_source, p_target, p_relation)
        if pending_pair:
            graph.setdefault(pending_pair[0], set()).add(pending_pair[1])
    stack = [child]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == parent:
            raise FamilyConflict("부모·자녀 관계가 순환할 수 없습니다.")
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))


def _insert_edge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    relationship: str,
    other_description: str | None = None,
) -> dict[str, Any]:
    a, b, stored_relation = _normalize(source_id, target_id, relationship)
    if _edge_between(conn, a, b) is not None:
        raise FamilyConflict("이미 가족으로 연결된 고객입니다. 기존 관계를 먼저 확인해 주세요.")
    _ensure_no_parent_cycle(conn, source_id, target_id, relationship)
    now = repo._now()
    edge_id = repo._new_id()
    encrypted_description = get_cipher().encrypt(other_description) if other_description else None
    try:
        conn.execute(
            "INSERT INTO family_relationships "
            "(id,customer_a_id,customer_b_id,relation_a_to_b,other_description,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (edge_id, a, b, stored_relation, encrypted_description, now, now),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise FamilyConflict("이미 가족으로 연결된 고객입니다. 기존 관계를 먼저 확인해 주세요.") from exc
        raise
    return {
        "id": edge_id,
        "customer_a_id": a,
        "customer_b_id": b,
        "relation_a_to_b": stored_relation,
        "other_description": other_description,
        "created_at": now,
        "updated_at": now,
    }


def _require_spouse(conn: sqlite3.Connection, source_id: str, spouse_id: str) -> None:
    row = _edge_between(conn, source_id, spouse_id)
    if row is None or _relation_for(row, source_id) != "SPOUSE":
        raise FamilyConflict("공동 자녀로 연결할 고객은 현재 고객의 배우자로 먼저 연결되어 있어야 합니다.")


def _require_existing_child(conn: sqlite3.Connection, source_id: str, child_id: str) -> None:
    row = _edge_between(conn, source_id, child_id)
    if row is None or _relation_for(row, source_id) != "CHILD":
        raise FamilyConflict("함께 연결할 고객은 현재 고객의 기존 자녀여야 합니다.")


def create_relationships(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    relationship: str,
    *,
    other_description: str | None = None,
    co_parent_customer_id: str | None = None,
    shared_child_ids: Iterable[str] = (),
    _manage_transaction: bool = True,
) -> list[dict[str, Any]]:
    shared_children = list(dict.fromkeys(shared_child_ids))
    if relationship != "CHILD" and co_parent_customer_id is not None:
        raise FamilyValidation("공동 자녀 선택은 자녀 관계에서만 사용할 수 있습니다.")
    if relationship != "SPOUSE" and shared_children:
        raise FamilyValidation("기존 자녀 함께 연결은 배우자 관계에서만 사용할 수 있습니다.")
    if relationship == "OTHER" and not other_description:
        raise FamilyValidation("기타 관계 설명을 입력해 주세요.")
    if relationship != "OTHER" and other_description:
        raise FamilyValidation("기타 관계에서만 설명을 입력할 수 있습니다.")

    try:
        if _manage_transaction:
            conn.execute("BEGIN IMMEDIATE")
        required_ids = {source_id, target_id}
        if co_parent_customer_id:
            required_ids.add(co_parent_customer_id)
        required_ids.update(shared_children)
        missing = [customer_id for customer_id in required_ids if not _customer_exists(conn, customer_id)]
        if missing:
            raise FamilyNotFound("연결할 고객을 찾을 수 없거나 현재 고객 데이터에 접근할 수 없습니다.")

        # Validate the whole compound request before the first write.
        if co_parent_customer_id:
            if co_parent_customer_id in {source_id, target_id}:
                raise FamilyValidation("공동 부모는 서로 다른 기존 고객이어야 합니다.")
            _require_spouse(conn, source_id, co_parent_customer_id)
            if _edge_between(conn, co_parent_customer_id, target_id) is not None:
                raise FamilyConflict("배우자와 선택한 자녀가 이미 가족으로 연결되어 있습니다.")
        for child_id in shared_children:
            if child_id in {source_id, target_id}:
                raise FamilyValidation("배우자나 자기 자신을 자녀로 함께 연결할 수 없습니다.")
            _require_existing_child(conn, source_id, child_id)
            if _edge_between(conn, target_id, child_id) is not None:
                raise FamilyConflict("배우자와 선택한 자녀가 이미 가족으로 연결되어 있습니다.")

        _normalize(source_id, target_id, relationship)
        if _edge_between(conn, source_id, target_id) is not None:
            raise FamilyConflict("이미 가족으로 연결된 고객입니다. 기존 관계를 먼저 확인해 주세요.")
        pending = [(source_id, target_id, relationship)]
        for child_id in shared_children:
            _ensure_no_parent_cycle(conn, target_id, child_id, "CHILD", pending)
            pending.append((target_id, child_id, "CHILD"))
        if co_parent_customer_id:
            _ensure_no_parent_cycle(conn, co_parent_customer_id, target_id, "CHILD", pending)

        created = [_insert_edge(conn, source_id, target_id, relationship, other_description)]
        if co_parent_customer_id:
            created.append(_insert_edge(conn, co_parent_customer_id, target_id, "CHILD"))
        for child_id in shared_children:
            created.append(_insert_edge(conn, target_id, child_id, "CHILD"))
        if _manage_transaction:
            conn.execute("COMMIT")
        return created
    except Exception:
        if _manage_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _stored_edge_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "customer_a_id": row["customer_a_id"],
        "customer_b_id": row["customer_b_id"],
        "relation_a_to_b": row["relation_a_to_b"],
        "other_description": _decrypt_description(row["other_description"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _batch_payload_digest(items: list[dict[str, Any]]) -> str:
    canonical_items = [
        {
            "related_customer_id": item["related_customer_id"],
            "relationship": item["relationship"],
            "other_description": item.get("other_description"),
            "co_parent_customer_id": item.get("co_parent_customer_id"),
            "shared_child_ids": sorted(item.get("shared_child_ids") or ()),
        }
        for item in items
    ]
    canonical_items.sort(
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    canonical = json.dumps(
        canonical_items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_relationship_batch(
    conn: sqlite3.Connection,
    source_id: str,
    idempotency_key: str,
    relationships: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Atomically bind one canonical request/result to a customer-scoped key."""
    items = list(relationships)
    if not items:
        raise FamilyValidation("하나 이상의 가족 관계가 필요합니다.")
    digest = _batch_payload_digest(items)
    created: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        ledger = conn.execute(
            "SELECT payload_digest,result_json FROM family_relationship_batch_idempotency "
            "WHERE customer_id=? AND scope=? AND idempotency_key=?",
            (source_id, _BATCH_SCOPE, idempotency_key),
        ).fetchone()
        if ledger is not None:
            if ledger["payload_digest"] != digest:
                raise FamilyConflict("같은 저장 키에 다른 가족 관계 내용은 사용할 수 없습니다.")
            edge_ids = json.loads(ledger["result_json"])
            replay_rows = []
            for edge_id in edge_ids:
                row = conn.execute("SELECT * FROM family_relationships WHERE id=?", (edge_id,)).fetchone()
                if row is None:
                    raise FamilyConflict("이 저장 키의 이전 가족 관계 결과가 더 이상 존재하지 않습니다.")
                replay_rows.append(_stored_edge_payload(row))
            conn.execute("COMMIT")
            return replay_rows, True
        for item in items:
            created.extend(create_relationships(
                conn,
                source_id,
                item["related_customer_id"],
                item["relationship"],
                other_description=item.get("other_description"),
                co_parent_customer_id=item.get("co_parent_customer_id"),
                shared_child_ids=item.get("shared_child_ids") or (),
                _manage_transaction=False,
            ))
        conn.execute(
            "INSERT INTO family_relationship_batch_idempotency "
            "(customer_id,scope,idempotency_key,payload_digest,result_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                source_id,
                _BATCH_SCOPE,
                idempotency_key,
                digest,
                json.dumps([item["id"] for item in created], separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute("COMMIT")
        return created, False
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _decrypt_description(value: str | None) -> str | None:
    return get_cipher().decrypt(value) if value else None


def list_relationships(conn: sqlite3.Connection, customer_id: str) -> list[dict[str, Any]]:
    if not _customer_exists(conn, customer_id):
        raise FamilyNotFound("고객을 찾을 수 없습니다.")
    rows = conn.execute(
        "SELECT * FROM family_relationships WHERE customer_a_id=? OR customer_b_id=? "
        "ORDER BY created_at,id",
        (customer_id, customer_id),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        related_id = row["customer_b_id"] if row["customer_a_id"] == customer_id else row["customer_a_id"]
        related = repo.get_customer(conn, related_id)
        if related is None:
            continue
        relationship = _relation_for(row, customer_id)
        result.append({
            "id": row["id"],
            "relationship": relationship,
            "relationship_label": RELATIONSHIP_LABELS[relationship],
            "other_description": _decrypt_description(row["other_description"]),
            "customer": {
                "id": related["id"],
                "name": related["name"],
                "phone": related.get("phone"),
                "birth_date": related.get("birth_date"),
                "gender": related.get("gender"),
                "customer_status": related.get("customer_status"),
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result


def delete_relationship(conn: sqlite3.Connection, customer_id: str, relationship_id: str) -> bool:
    row = conn.execute(
        "SELECT customer_a_id,customer_b_id FROM family_relationships WHERE id=?",
        (relationship_id,),
    ).fetchone()
    if row is None or customer_id not in {row["customer_a_id"], row["customer_b_id"]}:
        return False
    deleted = conn.execute("DELETE FROM family_relationships WHERE id=?", (relationship_id,)).rowcount
    conn.commit()
    return deleted == 1


def list_sync_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Stable mobile DTO payloads. Tokens/ownership stay in the existing sync layer."""
    return [
        {
            "id": row["id"],
            "customer_a_id": row["customer_a_id"],
            "customer_b_id": row["customer_b_id"],
            "relation_a_to_b": row["relation_a_to_b"],
            "other_description": _decrypt_description(row["other_description"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in conn.execute("SELECT * FROM family_relationships ORDER BY id").fetchall()
    ]
