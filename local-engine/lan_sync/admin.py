"""Local operator commands for pairing and revocation.

No network admin endpoint is exposed. Run this only on the PC that owns the sync DB.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .certificate import certificate_spki_sha256, ensure_certificate
from .store import SyncStore


def tls_rollover_ready(db_path: str | Path, cert_path: str | Path) -> bool:
    database = Path(db_path).resolve()
    certificate = Path(cert_path)
    if not database.is_file() or not certificate.is_file():
        return False
    spki_sha256 = certificate_spki_sha256(certificate)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sync_devices)").fetchall()
        }
        if "tls_identity_spki_sha256" not in columns:
            return False
        active_count, bound_count = connection.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN tls_identity_spki_sha256=? THEN 1 ELSE 0 END)
            FROM sync_devices
            WHERE revoked_at IS NULL
            """,
            (spki_sha256,),
        ).fetchone()
        # 활성 기기가 하나도 없으면 옛 인증서(leaf)에 의존하는 연결이 없다. 키는 그대로 두고 주소만 바꿔
        # 재발급해도 끊길 기기가 없으며, 해제된 기기는 PC 복구 코드 + PC 화면의 새 주소·인증서 SHA-256
        # 직접 대조(모바일 복구 화면)로만 다시 연결된다.
        if int(active_count) == 0:
            return True
        return int(bound_count or 0) == int(active_count)
    finally:
        connection.close()


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
    hide = sub.add_parser("hide-revoked-device")
    hide.add_argument("device_id")
    hide.add_argument("--confirmed", action="store_true", required=True)
    certificate = sub.add_parser("ensure-certificate")
    certificate.add_argument("--state-dir", required=True)
    certificate.add_argument("--host", action="append", required=True)
    certificate.add_argument("--valid-days", type=int, default=365)
    certificate.add_argument("--renew-before-days", type=int, default=30)

    args = parser.parse_args()

    if args.action == "ensure-certificate":
        cert_path = Path(args.state_dir) / "mobile-sync-cert.pem"
        result = ensure_certificate(
            args.state_dir,
            args.host,
            valid_days=args.valid_days,
            renew_before_days=args.renew_before_days,
            allow_address_reissue=bool(args.db) and tls_rollover_ready(args.db, cert_path),
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
        elif args.action == "hide-revoked-device":
            store.hide_revoked_device(args.device_id)
            print("hidden-from-list")

    finally:
        store.close()


if __name__ == "__main__":
    main()
