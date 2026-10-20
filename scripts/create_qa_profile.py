#!/usr/bin/env python3
"""Create a new fail-closed Insurance AI QA runtime marker."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

MARKER = ".insurance-ai-qa-profile.json"
REPARSE_POINT = 0x400
TLS_CERT_NAME = "qa-loopback-cert.pem"
TLS_KEY_NAME = "qa-loopback-key.pem"


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & REPARSE_POINT)


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def validate_root(root: Path) -> Path:
    raw = str(root)
    if not root.is_absolute() or raw.startswith(("\\\\", "//")):
        raise ValueError("QA root must be an absolute local path (UNC is forbidden)")
    for ancestor in [root, *root.parents]:
        if _is_reparse(ancestor):
            raise ValueError(f"symlink/junction/reparse path is forbidden: {ancestor}")
    resolved = root.resolve(strict=False)
    production_candidates = []
    for variable in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            production_candidates.extend(
                [Path(base) / "Insurance AI", Path(base) / "com.abc.insurance-ai"]
            )
    for production in production_candidates:
        if _overlap(resolved, production.resolve(strict=False)):
            raise ValueError(f"QA root overlaps a production path: {production}")
    if root.exists() and any(root.iterdir()):
        raise ValueError("QA root must not exist or must be empty")
    return resolved


def create_pinned_tls_identity(config_dir: Path, instance_id: str) -> None:
    """Create a per-profile localhost certificate; never reuse it outside this QA root."""
    config_dir.mkdir(parents=True, exist_ok=False)
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Insurance AI QA {instance_id}")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=True)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    key_path = config_dir / TLS_KEY_NAME
    cert_path = config_dir / TLS_CERT_NAME
    with key_path.open("xb") as handle:
        handle.write(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    if os.name != "nt":
        key_path.chmod(0o600)
    with cert_path.open("xb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--engine-port", required=True, type=int)
    parser.add_argument("--control-port", required=True, type=int)
    args = parser.parse_args()
    if not (1024 <= args.engine_port <= 65535 and 1024 <= args.control_port <= 65535):
        parser.error("ports must be in 1024..65535")
    if args.engine_port in (8420, 8790) or args.control_port in (8420, 8790):
        parser.error("production ports 8420/8790 are forbidden")
    if args.engine_port == args.control_port:
        parser.error("engine and control ports must differ")

    try:
        root = validate_root(args.root)
        root.mkdir(parents=True, exist_ok=True)
        instance_id = str(uuid.uuid4())
        create_pinned_tls_identity(root / "config", instance_id)
        marker = {
            "schema": 2,
            "instance_id": instance_id,
            "profile_fingerprint": secrets.token_hex(32),
            "transport": "pinned-tls-v1",
        }
        with (root / MARKER).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError) as error:
        print(f"QA marker creation refused: {error}", file=sys.stderr)
        return 1

    print(json.dumps({
        "root": str(root),
        "instance_id": instance_id,
        "engine_port": args.engine_port,
        "control_port": args.control_port,
        "launch_args": [
            "--qa-profile-dir", str(root),
            "--qa-instance-id", instance_id,
            "--qa-engine-port", str(args.engine_port),
            "--qa-control-port", str(args.control_port),
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
