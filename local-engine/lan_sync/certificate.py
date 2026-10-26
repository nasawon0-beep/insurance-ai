"""Per-install TLS identity for the personal LAN sync server."""
from __future__ import annotations

import getpass
import hashlib
import ipaddress
import json
import os
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_NAME = "mobile-sync-cert.pem"
KEY_NAME = "mobile-sync-key.pem"
METADATA_NAME = "mobile-sync-certificate.json"
TRANSACTION_NAME = ".mobile-sync-certificate.transaction.json"


def _not_valid_after(cert: x509.Certificate) -> datetime:
    value = getattr(cert, "not_valid_after_utc", None)
    return value if value is not None else cert.not_valid_after.replace(tzinfo=timezone.utc)


def certificate_spki_sha256(cert_path: str | Path) -> str:
    certificate = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()


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


def _verify_private_key_permissions(path: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["icacls.exe", str(path), "/verify"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("mobile sync private key ACL verification failed")
    else:
        if stat.S_IMODE(path.stat().st_mode) != stat.S_IRUSR | stat.S_IWUSR:
            raise OSError("mobile sync private key permission verification failed")


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name == "nt" and os.path.lexists(path):
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _assert_no_follow(paths: tuple[Path, ...], *, transaction_artifacts: bool = False) -> None:
    for path in paths:
        if _is_reparse_or_symlink(path):
            if transaction_artifacts:
                raise OSError(f"mobile sync transaction artifact must not be a symlink or reparse point: {path.name}")
            raise OSError(f"mobile sync certificate files must not be symlinks or reparse points: {path.name}")


def _remove_if_present(path: Path) -> None:
    _assert_no_follow((path,))
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _transaction_paths(root: Path) -> tuple[Path, ...]:
    cert_path = root / CERT_NAME
    metadata_path = root / METADATA_NAME
    return (
        root / TRANSACTION_NAME,
        cert_path.with_suffix(".rollback"),
        metadata_path.with_suffix(".rollback"),
        cert_path.with_suffix(".tmp"),
        metadata_path.with_suffix(".tmp"),
        cert_path.with_suffix(".restore.tmp"),
        metadata_path.with_suffix(".restore.tmp"),
        root / ".mobile-sync-result-cert.tmp",
    )


def _load_transaction(journal: Path) -> dict[str, object]:
    try:
        state = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("mobile sync certificate transaction journal is invalid; manual recovery required") from exc
    if not isinstance(state, dict) or type(state.get("version", 1)) is not int or state.get("version", 1) not in (1, 2):
        raise OSError("unsupported mobile sync certificate transaction journal")
    required_fields = {"cert_existed", "metadata_existed"}
    if state.get("version", 1) == 2:
        required_fields.add("new_key_created")
    if not required_fields.issubset(state):
        raise OSError("mobile sync certificate transaction journal has invalid fields")
    for key in ("cert_existed", "metadata_existed", "new_key_created"):
        if key in state and not isinstance(state[key], bool):
            raise OSError("mobile sync certificate transaction journal has invalid fields")
    return state


def _restore_backup(backup: Path, final_path: Path, restore_tmp: Path) -> None:
    data = backup.read_bytes()
    restore_tmp.write_bytes(data)
    os.replace(restore_tmp, final_path)
    if final_path.read_bytes() != data:
        raise OSError(f"mobile sync rollback verification failed: {final_path.name}")


def _restore_transaction(root: Path, state: dict[str, object]) -> None:
    journal = root / TRANSACTION_NAME
    cert_path = root / CERT_NAME
    key_path = root / KEY_NAME
    metadata_path = root / METADATA_NAME
    cert_backup = cert_path.with_suffix(".rollback")
    metadata_backup = metadata_path.with_suffix(".rollback")
    cert_restore = cert_path.with_suffix(".restore.tmp")
    metadata_restore = metadata_path.with_suffix(".restore.tmp")
    _assert_no_follow((cert_path, key_path, metadata_path))
    _assert_no_follow((
        *_transaction_paths(root),
    ), transaction_artifacts=True)

    required = []
    if state.get("cert_existed"):
        required.append(cert_backup)
    if state.get("metadata_existed"):
        required.append(metadata_backup)
    if any(not path.is_file() for path in required):
        raise OSError("mobile sync certificate transaction backup is missing; startup blocked")
    backup_cert = None
    if state.get("cert_existed"):
        try:
            backup_cert = x509.load_pem_x509_certificate(cert_backup.read_bytes())
            backup_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise OSError("mobile sync rollback certificate or private key is invalid; startup blocked") from exc
        if not isinstance(backup_key, ec.EllipticCurvePrivateKey):
            raise OSError("mobile sync rollback private key has an unsupported type; startup blocked")
        cert_spki = backup_cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_spki = backup_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if cert_spki != key_spki:
            raise OSError("mobile sync rollback certificate does not match the private key; startup blocked")
    if state.get("metadata_existed"):
        try:
            backup_metadata = json.loads(metadata_backup.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OSError("mobile sync rollback metadata is invalid; startup blocked") from exc
        if not isinstance(backup_metadata, dict):
            raise OSError("mobile sync rollback metadata is invalid; startup blocked")
        if backup_cert is not None:
            cert_fingerprint = hashlib.sha256(
                backup_cert.public_bytes(serialization.Encoding.DER)
            ).hexdigest()
            if backup_metadata.get("fingerprint") != cert_fingerprint:
                raise OSError("mobile sync rollback metadata does not match the certificate; startup blocked")

    if state.get("metadata_existed"):
        _restore_backup(metadata_backup, metadata_path, metadata_restore)
    else:
        _remove_if_present(metadata_path)
    if state.get("cert_existed"):
        _restore_backup(cert_backup, cert_path, cert_restore)
    else:
        _remove_if_present(cert_path)
    if state.get("new_key_created"):
        _remove_if_present(key_path)

    if state.get("cert_existed") and cert_path.read_bytes() != cert_backup.read_bytes():
        raise OSError("mobile sync certificate rollback did not restore the certificate")
    if state.get("metadata_existed") and metadata_path.read_bytes() != metadata_backup.read_bytes():
        raise OSError("mobile sync certificate rollback did not restore metadata")
    if state.get("new_key_created") and os.path.lexists(key_path):
        raise OSError("mobile sync certificate rollback did not remove the new key")

    for path in (
        cert_path.with_suffix(".tmp"),
        metadata_path.with_suffix(".tmp"),
        cert_restore,
        metadata_restore,
        root / ".mobile-sync-result-cert.tmp",
        journal,
        cert_backup,
        metadata_backup,
    ):
        _remove_if_present(path)


def _recover_interrupted_transaction(root: Path) -> None:
    journal = root / TRANSACTION_NAME
    artifacts = _transaction_paths(root)
    _assert_no_follow((root / CERT_NAME, root / KEY_NAME, root / METADATA_NAME))
    _assert_no_follow(artifacts, transaction_artifacts=True)
    if journal.is_file():
        _restore_transaction(root, _load_transaction(journal))
        return
    for path in artifacts[1:]:
        _remove_if_present(path)


def _commit_certificate_tuple(
    root: Path,
    cert_path: Path,
    key_path: Path,
    metadata_path: Path,
    certificate_pem: bytes,
    metadata: dict[str, object],
    new_key_temp: Path | None = None,
) -> None:
    cert_tmp = cert_path.with_suffix(".tmp")
    metadata_tmp = metadata_path.with_suffix(".tmp")
    cert_backup = cert_path.with_suffix(".rollback")
    metadata_backup = metadata_path.with_suffix(".rollback")
    journal = root / TRANSACTION_NAME
    cert_existed = cert_path.is_file()
    metadata_existed = metadata_path.is_file()
    _assert_no_follow((cert_path, key_path, metadata_path))
    _assert_no_follow(_transaction_paths(root), transaction_artifacts=True)
    try:
        cert_tmp.write_bytes(certificate_pem)
        metadata_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        x509.load_pem_x509_certificate(cert_tmp.read_bytes())
        json.loads(metadata_tmp.read_text(encoding="utf-8"))
        if cert_existed:
            cert_backup.write_bytes(cert_path.read_bytes())
        if metadata_existed:
            metadata_backup.write_bytes(metadata_path.read_bytes())
        journal.write_text(json.dumps({
            "version": 2,
            "cert_existed": cert_existed,
            "metadata_existed": metadata_existed,
            "new_key_created": new_key_temp is not None,
        }), encoding="utf-8")
        if new_key_temp is not None:
            new_key_temp.replace(key_path)
        cert_tmp.replace(cert_path)
        metadata_tmp.replace(metadata_path)
    except BaseException:
        if journal.is_file():
            state = _load_transaction(journal)
            _restore_transaction(root, state)
        else:
            for path in (cert_tmp, metadata_tmp, cert_backup, metadata_backup):
                _remove_if_present(path)
            if new_key_temp is not None:
                _remove_if_present(key_path)
        raise
    else:
        for path in (journal, cert_backup, metadata_backup, cert_tmp, metadata_tmp):
            _remove_if_present(path)


def _certificate_result(
    cert_path: Path,
    key_path: Path,
    hosts: list[str],
    created: bool,
    reason: str,
) -> dict[str, object]:
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    der = cert.public_bytes(serialization.Encoding.DER)
    expires_at = _not_valid_after(cert)
    return {
        "cert_path": str(cert_path.resolve()),
        "key_path": str(key_path.resolve()),
        "fingerprint": hashlib.sha256(der).hexdigest(),
        "spki_sha256": certificate_spki_sha256(cert_path),
        "expires_at": int(expires_at.timestamp()),
        "hosts": hosts,
        "created": created,
        "reason": reason,
    }


def ensure_certificate(
    directory: str | Path,
    hosts: list[str],
    *,
    valid_days: int = 365,
    renew_before_days: int = 30,
    allow_address_reissue: bool = False,
) -> dict[str, object]:
    if not 7 <= valid_days <= 825:
        raise ValueError("certificate validity must be between 7 and 825 days")
    if not 1 <= renew_before_days < valid_days:
        raise ValueError("renew-before days must be positive and less than validity")
    normalized_hosts = _validated_hosts(hosts)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_symlink(root):
        raise OSError("mobile sync certificate directory must not be a symlink or reparse point")
    _recover_interrupted_transaction(root)
    cert_path = root / CERT_NAME
    key_path = root / KEY_NAME
    metadata_path = root / METADATA_NAME
    if any(_is_reparse_or_symlink(path) for path in (cert_path, key_path, metadata_path)):
        raise OSError("mobile sync certificate files must not be symlinks or reparse points")
    now = datetime.now(timezone.utc)

    reusable = False
    address_changed = False
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
            if not allow_address_reissue:
                raise ValueError(
                    "mobile sync address changed; certificate confirmation required before replacing the existing identity"
                )
            address_changed = True
        reusable = not address_changed and expires_at > now + timedelta(days=renew_before_days)

    new_key_temp: Path | None = None
    if not reusable:
        if existing_private_key is not None:
            private_key = existing_private_key
        elif key_path.is_file():
            private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(private_key, ec.EllipticCurvePrivateKey):
                raise ValueError("existing mobile sync key has an unsupported type")
        else:
            private_key = ec.generate_private_key(ec.SECP256R1())
            descriptor, temp_name = tempfile.mkstemp(prefix=".mobile-sync-key-", suffix=".tmp", dir=root)
            os.close(descriptor)
            new_key_temp = Path(temp_name)
            try:
                _restrict_private_key(new_key_temp)
                _verify_private_key_permissions(new_key_temp)
                new_key_temp.write_bytes(private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ))
                serialization.load_pem_private_key(new_key_temp.read_bytes(), password=None)
            except BaseException:
                _remove_if_present(new_key_temp)
                raise
        if new_key_temp is None:
            _restrict_private_key(key_path)
            _verify_private_key_permissions(key_path)
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
    else:
        _restrict_private_key(key_path)
        _verify_private_key_permissions(key_path)

    reason = "address_changed" if address_changed else ("reused" if reusable else "issued")
    if not reusable:
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        temporary_cert = root / ".mobile-sync-result-cert.tmp"
        temporary_cert.write_bytes(certificate_pem)
        try:
            result = _certificate_result(temporary_cert, new_key_temp or key_path, normalized_hosts, True, reason)
            result["cert_path"] = str(cert_path.resolve())
            result["key_path"] = str(key_path.resolve())
            metadata = {key: value for key, value in result.items() if key != "key_path"}
            try:
                _commit_certificate_tuple(
                    root,
                    cert_path,
                    key_path,
                    metadata_path,
                    certificate_pem,
                    metadata,
                    new_key_temp,
                )
            except BaseException:
                if existing_private_key is None:
                    _remove_if_present(key_path)
                raise
        finally:
            _remove_if_present(temporary_cert)
            if new_key_temp is not None:
                _remove_if_present(new_key_temp)
    else:
        result = _certificate_result(cert_path, key_path, normalized_hosts, False, reason)
        metadata = {key: value for key, value in result.items() if key != "key_path"}
        temporary_metadata = metadata_path.with_suffix(".tmp")
        try:
            temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary_metadata.replace(metadata_path)
        finally:
            _remove_if_present(temporary_metadata)
    return result
