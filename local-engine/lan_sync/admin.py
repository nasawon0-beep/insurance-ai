"""Local operator commands for pairing and revocation.

No network admin endpoint is exposed. Run this only on the PC that owns the sync DB.
"""
from __future__ import annotations

import argparse

from .store import SyncStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create-code")
    create.add_argument("--pc-name", required=True)
    create.add_argument("--ttl", type=int, default=300)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("device_id")
    sub.add_parser("seed-demo")
    args = parser.parse_args()

    store = SyncStore(args.db)
    store.initialize()
    try:
        if args.action == "create-code":
            result = store.create_pairing_code(args.pc_name, args.ttl)
            print(f"pairing_code={result.code}")
            print(f"expires_at={result.expires_at}")
        elif args.action == "revoke":
            store.revoke_device(args.device_id)
            print("revoked")
        elif args.action == "seed-demo":
            store.seed_synthetic_snapshot("customer", "demo-customer-1", {
                "name": "합성 고객", "phone": "010-0000-0000", "address": "합성 주소",
                "memo": "테스트 전용", "customer_status": "가망",
            })
            store.seed_synthetic_snapshot("policy", "demo-policy-1", {
                "customer_id": "demo-customer-1", "insurer": "합성보험",
                "product_name": "합성 보장상품", "premium": 12345, "status": "ACTIVE",
            })
            store.seed_synthetic_snapshot("consultation", "demo-consultation-1", {
                "customer_id": "demo-customer-1", "consulted_at": "2026-10-21T00:00:00Z",
                "channel": "테스트", "title": "합성 상담", "content": "합성 상담 기록",
            })
            store.seed_synthetic_snapshot("coverage_analysis", "demo-analysis-1", {
                "customer_id": "demo-customer-1", "generated_at": "2026-10-21T00:00:00Z",
                "summary": {"status": "synthetic", "note": "PDF 분석은 PC 전용"},
                "source_hash": "synthetic-only",
            })
            print("seeded_demo_entities=4")
    finally:
        store.close()


if __name__ == "__main__":
    main()
