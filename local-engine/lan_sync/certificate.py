"""Per-install TLS identity for the personal LAN sync server."""
from __future__ import annotations

import getpass
import hashlib
import ipaddress
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_NAME = "mobile-sync-cert.pem"
KEY_NAME = "mobile-sync-key.pem"
METADATA_NAME = "mobile-sync-certificate.json"


def _not_valid_after(cert: x509.Certificate) -> datetime:
    value = getattr(cert, "not_valid_after_utc", None)
    return value if value is not None else cert.not_valid_after.replace(tzinfo=timezone.utc)


def _validated_hosts(hosts: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in hosts:
        address = ipaddress.ip_address(value.strip())
        if address.version != 4 or not (address.is_private or address.is_loopback):
            raise ValueError("mobile sync certificate accepts only private or loopback IPv4 addresses")
        text = str(address)
        if text not in normalized:
            normalized.append(text)
    if not normalized:
        raise ValueError("at least one private IPv4 address is required")
    return sorted(normalized)


def _restrict_private_key(path: Path) -> None:
    if os.name == "nt":
        account = getpass.getuser()
        result = subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("failed to restrict mobile sync private key ACL")
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _certificate_result(cert_path: Path, key_path: Path, hosts: list[str], created: bool) -> dict[str, object]:
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    expires_at = _not_valid_after(cert)
    return {
        "cert_path": str(cert_path.resolve()),
        "key_path": str(key_path.resolve()),
        "fingerprint": hashlib.sha256(der).hexdigest(),
        "expires_at": int(expires_at.timestamp()),
        "hosts": hosts,
        "created": created,
    }


def ensure_certificate(
    directory: str | Path,
    hosts: list[str],
    *,
    valid_days: int = 365,
    renew_before_days: int = 30,
) -> dict[str, object]:
    if not 7 <= valid_days <= 825:
        raise ValueError("certificate validity must be between 7 and 825 days")
    if not 1 <= renew_before_days < valid_days:
        raise ValueError("renew-before days must be positive and less than validity")
    normalized_hosts = _validated_hosts(hosts)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    cert_path = root / CERT_NAME
    key_path = root / KEY_NAME
    metadata_path = root / METADATA_NAME
    now = datetime.now(timezone.utc)

    reusable = False
    existing_private_key: ec.EllipticCurvePrivateKey | None = None
    if cert_path.is_file() and key_path.is_file():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            loaded_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except ValueError as exc:
            raise ValueError(
                "existing mobile sync certificate or private key is invalid; restore the matching backup before starting mobile sync"
            ) from exc
        if not isinstance(loaded_key, ec.EllipticCurvePrivateKey):
            raise ValueError("existing mobile sync key has an unsupported type")
        cert_public_key = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_public_key = loaded_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if cert_public_key != key_public_key:
            raise ValueError(
                "existing mobile sync certificate public key does not match the private key; restore the matching backup before starting mobile sync"
            )
        existing_private_key = loaded_key
        try:
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            current_hosts = sorted(str(item) for item in sans.get_values_for_type(x509.IPAddress))
        except x509.ExtensionNotFound:
            current_hosts = []
        expires_at = _not_valid_after(cert)
        if current_hosts != normalized_hosts:
            raise ValueError(
                "mobile sync address changed; certificate confirmation required before replacing the existing identity"
            )
        reusable = expires_at > now + timedelta(days=renew_before_days)

    if not reusable:
        if existing_private_key is not None:
            private_key = existing_private_key
        elif key_path.is_file():
            private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(private_key, ec.EllipticCurvePrivateKey):
                raise ValueError("existing mobile sync key has an unsupported type")
        else:
            private_key = ec.generate_private_key(ec.SECP256R1())
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        _restrict_private_key(key_path)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MoaDoom Personal LAN Sync")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=valid_days))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host)) for host in normalized_hosts]),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        temporary = cert_path.with_suffix(".tmp")
        temporary.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        temporary.replace(cert_path)
    else:
        _restrict_private_key(key_path)

    result = _certificate_result(cert_path, key_path, normalized_hosts, not reusable)
    metadata = {key: value for key, value in result.items() if key != "key_path"}
    temporary_metadata = metadata_path.with_suffix(".tmp")
    temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_metadata.replace(metadata_path)
    return result
