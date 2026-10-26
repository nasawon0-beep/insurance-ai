"""Local operator commands for pairing and revocation.

No network admin endpoint is exposed. Run this only on the PC that owns the sync DB.
"""
from __future__ import annotations

import argparse
import json

from .certificate import ensure_certificate
from .store import SyncStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create-code")
    create.add_argument("--pc-name", required=True)
    create.add_argument("--ttl", type=int, default=300)
    recover = sub.add_parser("create-recovery-code")
    recover.add_argument("device_id")
    recover.add_argument("--pc-instance-id", required=True)
    recover.add_argument("--ttl", type=int, default=120)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("device_id")
    sub.add_parser("list-devices")
    certificate = sub.add_parser("ensure-certificate")
    certificate.add_argument("--state-dir", required=True)
    certificate.add_argument("--host", action="append", required=True)
    certificate.add_argument("--valid-days", type=int, default=365)
    certificate.add_argument("--renew-before-days", type=int, default=30)

    args = parser.parse_args()

    if args.action == "ensure-certificate":
        result = ensure_certificate(
            args.state_dir,
            args.host,
            valid_days=args.valid_days,
            renew_before_days=args.renew_before_days,
        )
        print(json.dumps(result))
        return
    if not args.db:
        parser.error("--db is required for device and pairing commands")
    store = SyncStore(args.db)
    store.initialize()
    try:
        if args.action == "create-code":
            result = store.create_pairing_code(args.pc_name, args.ttl)
            print(f"pairing_code={result.code}")
            print(f"expires_at={result.expires_at}")
        elif args.action == "create-recovery-code":
            result = store.create_recovery_code(args.device_id, args.pc_instance_id, args.ttl)
            print(f"recovery_code={result.code}")
            print(f"expires_at={result.expires_at}")
        elif args.action == "revoke":
            store.revoke_device(args.device_id)
            print("revoked")
        elif args.action == "list-devices":
            print(json.dumps(store.list_devices(), ensure_ascii=False))

    finally:
        store.close()


if __name__ == "__main__":
    main()
