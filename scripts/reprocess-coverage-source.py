"""Safely backfill one customer's coverage source metadata from a local PDF.

Default is dry-run. --apply requires an unused --backup path and exact
--confirm-customer-id. It never deletes rows and never creates analysis runs;
after a successful apply, use the generated recalculate payload with the local
engine to create a new immutable analysis revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))
from database.coverage_analysis_parser import parse_coverage_analysis  # noqa: E402
from database.policy_coverages import (  # noqa: E402
    DocumentType,
    detect_candidate_pages,
    parse_policy_coverages_from_pdf,
    sync_source_occurrences,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--confirm-customer-id")
    return parser.parse_args()


def qmark(count: int) -> str:
    return ",".join("?" for _ in range(count))


def main() -> int:
    args = parse_args()
    source_hash = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    parsed_rows, parsed_occurrences = parse_policy_coverages_from_pdf(
        str(args.pdf), args.pdf.name, source_hash, with_occurrences=True
    )
    observed = parse_coverage_analysis(
        str(args.pdf), detect_candidate_pages(str(args.pdf), DocumentType.COVERAGE_ANALYSIS_FULL)
    )
    uri = f"file:{args.db.resolve().as_posix()}?mode={'rw' if args.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    if not args.apply:
        conn.execute("PRAGMA query_only = ON")
    required = {
        "rider_name_raw", "standard_name_raw", "standard_name_extracted_raw", "standard_name_verification", "source_standard_name", "source_occurrence_key",
        "source_standard_names_json", "source_type", "source_link_status", "review_status",
    }
    columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_coverages)")}
    missing_columns = sorted(required - columns)
    occurrence_table_exists = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coverage_source_occurrences'"
    ).fetchone())
    if not occurrence_table_exists:
        missing_columns.append("coverage_source_occurrences(table)")
    existing = conn.execute(
        "SELECT * FROM policy_coverages WHERE customer_id=? AND source_hash=? ORDER BY source_page,rider_no,id",
        (args.customer_id, source_hash),
    ).fetchall() if not missing_columns else []
    by_key: dict[tuple, list[sqlite3.Row]] = {}
    for row in existing:
        by_key.setdefault((row["source_page"], str(row["rider_no"] or ""), row["amount"]), []).append(row)
    matches = []
    unmatched = []
    ambiguous = []
    for parsed in parsed_rows:
        key = (parsed.get("source_page"), str(parsed.get("rider_no") or ""), parsed.get("amount"))
        candidates = by_key.get(key, [])
        if len(candidates) == 1:
            matches.append((candidates[0], parsed))
        elif not candidates:
            unmatched.append(key)
        else:
            ambiguous.append(key)
    payload = {
        "force": True,
        "audience": "internal",
        "source": "coverage_source_recovery",
        "observed_summary": [
            {
                "coverage_name": row.coverage_name,
                "source_coverage_name": row.source_coverage_name,
                "amount": row.amount,
                "amount_text": row.amount_text,
                "amount_status": row.amount_status,
                "source_status": row.source_status,
                "source_page": row.source_page,
                "recommended_amount": row.recommended_amount,
                "recommended_amount_text": row.recommended_amount_text,
            }
            for row in observed
        ],
    }
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(args.db),
        "pdf_sha256": source_hash,
        "customer_id": args.customer_id,
        "parsed_ledger_count": len(parsed_rows),
        "parsed_occurrence_count": len(parsed_occurrences),
        "occurrence_resolution_counts": {
            status: sum(1 for row in parsed_occurrences if row.get("resolution_status") == status)
            for status in sorted({str(row.get("resolution_status")) for row in parsed_occurrences})
        },
        "existing_source_count": len(existing),
        "matched_count": len(matches),
        "unmatched_keys": unmatched,
        "ambiguous_keys": ambiguous,
        "missing_columns": missing_columns,
        "recalculate_payload": payload,
        "applied": False,
    }
    if args.apply:
        if args.confirm_customer_id != args.customer_id:
            raise SystemExit("--confirm-customer-id must exactly equal --customer-id")
        if not args.backup:
            raise SystemExit("--backup is required with --apply")
        if args.backup.exists():
            raise SystemExit("backup path already exists; refusing to overwrite")
        if missing_columns or unmatched or ambiguous or len(matches) != len(parsed_rows):
            raise SystemExit("source gate failed; refusing apply")
        conn.commit()
        conn.close()
        source = sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)
        backup = sqlite3.connect(args.backup)
        source.backup(backup)
        backup.close(); source.close()
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            for old, parsed in matches:
                conn.execute(
                    """UPDATE policy_coverages SET rider_name=?, rider_name_raw=?, standard_name=?,
                       standard_name_raw=?, standard_name_extracted_raw=?, standard_name_verification=?,
                       source_standard_name=?, source_standard_names_json=?,
                       source_occurrence_key=?, source_type=?, source_link_status=?, review_status=?, amount_status=?, raw_text=?,
                       updated_at=datetime('now') WHERE id=? AND customer_id=?""",
                    (parsed.get("rider_name"), parsed.get("rider_name_raw"), parsed.get("standard_name"),
                     parsed.get("standard_name_raw"), parsed.get("standard_name_extracted_raw"),
                     parsed.get("standard_name_verification"), parsed.get("source_standard_name"),
                     parsed.get("source_standard_names_json") or "[]", parsed.get("source_occurrence_key"), parsed.get("source_type"),
                     parsed.get("source_link_status"), parsed.get("review_status"), parsed.get("amount_status"),
                     parsed.get("raw_text"), old["id"], args.customer_id),
                )
            report["source_occurrences"] = sync_source_occurrences(
                conn, args.customer_id, parsed_occurrences
            )
            conn.commit()
            report["applied"] = True
            report["backup"] = str(args.backup)
        except Exception:
            conn.rollback()
            raise
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["mode", "parsed_ledger_count", "existing_source_count", "matched_count", "applied"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
