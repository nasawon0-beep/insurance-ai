"""고객별 중복 보험계약 정리 스크립트.

기본은 dry-run이며, --apply를 줘야 UPDATE/DELETE를 실행한다.
중복 기준: insurer + product_name + policy_number + start_date
대표 선정: policy_coverages 연결 수가 많은 계약, 동률이면 updated_at 최신 계약.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from . import db, repo
except ImportError:  # python database/dedupe_policies.py 직접 실행 지원
    import db  # type: ignore
    import repo  # type: ignore

DEFAULT_CUSTOMER_ID = "29c0c576fb204713974173d0cdc45649"


def _key(policy: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(policy.get("insurer") or "").strip(),
        str(policy.get("product_name") or "").strip(),
        str(policy.get("policy_number") or "").strip(),
        str(policy.get("start_date") or "").strip(),
    )


def _coverage_counts(conn: sqlite3.Connection, policy_ids: list[str]) -> dict[str, int]:
    if not policy_ids:
        return {}
    placeholders = ",".join("?" for _ in policy_ids)
    rows = conn.execute(
        f"SELECT policy_id, COUNT(*) AS c FROM policy_coverages WHERE policy_id IN ({placeholders}) GROUP BY policy_id",
        policy_ids,
    ).fetchall()
    return {r["policy_id"]: int(r["c"] or 0) for r in rows}


def plan(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any]:
    policies = repo.list_policies(conn, customer_id)
    counts = _coverage_counts(conn, [p["id"] for p in policies])
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for policy in policies:
        groups[_key(policy)].append(policy)

    duplicate_groups = []
    for key, rows in groups.items():
        if len(rows) <= 1:
            continue
        ranked = sorted(
            rows,
            key=lambda p: (counts.get(p["id"], 0), str(p.get("updated_at") or ""), str(p.get("created_at") or "")),
            reverse=True,
        )
        keep = ranked[0]
        remove = ranked[1:]
        duplicate_groups.append({
            "key": key,
            "keep": keep["id"],
            "keep_coverage_count": counts.get(keep["id"], 0),
            "remove": [p["id"] for p in remove],
            "remove_coverage_count": {p["id"]: counts.get(p["id"], 0) for p in remove},
        })

    remove_ids = [pid for group in duplicate_groups for pid in group["remove"]]
    return {
        "customer_id": customer_id,
        "before_count": len(policies),
        "after_count": len(policies) - len(remove_ids),
        "duplicate_group_count": len(duplicate_groups),
        "remove_count": len(remove_ids),
        "groups": duplicate_groups,
    }


def apply_plan(conn: sqlite3.Connection, dedupe_plan: dict[str, Any]) -> dict[str, Any]:
    updated_coverages = 0
    deleted_policies = 0
    conn.execute("BEGIN")
    try:
        for group in dedupe_plan["groups"]:
            keep = group["keep"]
            for remove_id in group["remove"]:
                cur = conn.execute("UPDATE policy_coverages SET policy_id = ? WHERE policy_id = ?", (keep, remove_id))
                updated_coverages += cur.rowcount
                cur = conn.execute("DELETE FROM policies WHERE id = ? AND customer_id = ?", (remove_id, dedupe_plan["customer_id"]))
                deleted_policies += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    after = conn.execute("SELECT COUNT(*) AS c FROM policies WHERE customer_id = ?", (dedupe_plan["customer_id"],)).fetchone()["c"]
    return {"updated_coverages": updated_coverages, "deleted_policies": deleted_policies, "after_count": after}


def main() -> int:
    parser = argparse.ArgumentParser(description="중복 보험계약 정리")
    parser.add_argument("--customer-id", default=DEFAULT_CUSTOMER_ID)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--apply", action="store_true", help="실제 UPDATE/DELETE 실행")
    parser.add_argument("--no-backup", action="store_true", help="--apply 시 DB 백업 생략")
    args = parser.parse_args()

    db_path = db.resolve_db_path(args.db_path)
    conn = db.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        dedupe_plan = plan(conn, args.customer_id)
        print(f"DB: {db_path}")
        print(f"customer_id: {dedupe_plan['customer_id']}")
        print(f"policies: {dedupe_plan['before_count']} -> {dedupe_plan['after_count']}")
        print(f"duplicate_groups: {dedupe_plan['duplicate_group_count']}, remove: {dedupe_plan['remove_count']}")
        for group in dedupe_plan["groups"]:
            print(f"KEEP {group['keep']} cov={group['keep_coverage_count']} KEY={group['key']}")
            for remove_id in group["remove"]:
                print(f"  DELETE {remove_id} cov={group['remove_coverage_count'][remove_id]}")

        if not args.apply:
            print("dry-run only. 실행하려면 --apply를 추가하세요.")
            return 0

        if not args.no_backup:
            backup = Path(str(db_path) + ".bak-dedupe-policies")
            shutil.copy2(db_path, backup)
            print(f"backup: {backup}")
        result = apply_plan(conn, dedupe_plan)
        print(f"applied: updated_coverages={result['updated_coverages']}, deleted_policies={result['deleted_policies']}, after_count={result['after_count']}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
