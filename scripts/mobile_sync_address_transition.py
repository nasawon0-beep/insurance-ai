from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

RULE_NAME = "MoaDoom hotspot QA temporary TCP 55902"


def discover_network() -> dict[str, object]:
    script = (
        "Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and "
        "$null -ne $_.IPv4DefaultGateway -and $null -ne $_.IPv4Address } | "
        "ForEach-Object { [PSCustomObject]@{ interface_index=$_.InterfaceIndex; "
        "host=$_.IPv4Address.IPAddress; gateway=$_.IPv4DefaultGateway.NextHop } } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
    )
    parsed = json.loads(result.stdout)
    candidates = parsed if isinstance(parsed, list) else [parsed]
    candidates = [item for item in candidates if item and item.get("host") and item.get("gateway")]
    if len(candidates) != 1:
        raise RuntimeError(f"expected exactly one active IPv4 default route, found {len(candidates)}")
    return candidates[0]


def stage_transition(state_path: Path, stage_dir: Path, network: dict[str, object]) -> dict[str, object]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    root = Path(state["root"])
    db_path = Path(state["db"])
    active_cert = Path(state["cert"])
    active_key = Path(state["key"])
    if not root.is_dir() or db_path.parent != root or active_cert.parent != root or active_key.parent != root:
        raise RuntimeError("runtime state paths do not share the declared root")
    for path in (db_path, active_cert, active_key):
        if not path.is_file():
            raise RuntimeError(f"runtime file missing: {path.name}")

    host = str(network["host"])
    gateway = str(network["gateway"])
    ipaddress.IPv4Address(host)
    ipaddress.IPv4Address(gateway)
    port = int(state["port"])
    if not (1 <= port <= 65535):
        raise RuntimeError("invalid runtime port")

    stage_dir.mkdir(parents=True, exist_ok=True)
    staged_cert = stage_dir / "tls-cert.pem"
    staged_key = stage_dir / "tls-key.pem"
    plan_path = stage_dir / "transition-plan.json"
    if any(path.exists() for path in (staged_cert, staged_key, plan_path)):
        raise RuntimeError("stage destination already contains transition artifacts")

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MoaDoom LAN Sync")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address(host)),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    staged_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    staged_cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    der = certificate.public_bytes(serialization.Encoding.DER)
    pin = hashlib.sha256(der).hexdigest()

    python_program = str(Path(sys.executable).resolve())
    firewall_delete = f'netsh advfirewall firewall delete rule name="{RULE_NAME}"'
    firewall_add = (
        f'netsh advfirewall firewall add rule name="{RULE_NAME}" dir=in action=allow '
        f'profile=private protocol=TCP localport={port} localip={host} remoteip={gateway} '
        f'program="{python_program}" edge=no enable=yes'
    )
    plan: dict[str, object] = {
        "mode": "staged-only",
        "source_state": str(state_path.resolve()),
        "runtime_root": str(root.resolve()),
        "old_host": state["host"],
        "new_host": host,
        "remote_ip": gateway,
        "interface_index": int(str(network["interface_index"])),
        "port": port,
        "staged_cert": str(staged_cert.resolve()),
        "staged_key": str(staged_key.resolve()),
        "certificate_sha256": pin,
        "certificate_sans": [host, "127.0.0.1"],
        "active_cert_target": str(active_cert.resolve()),
        "active_key_target": str(active_key.resolve()),
        "firewall_delete": firewall_delete,
        "firewall_add": firewall_add,
        "requires_server_stopped_before_apply": True,
        "runtime_state_patch": {"host": host},
        "live_state_modified": False,
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage, but never apply, a LAN sync address/certificate transition")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--stage-dir", type=Path, required=True)
    parser.add_argument("--network-json", type=Path, help="isolated-test input; omit on a live Windows host")
    args = parser.parse_args()
    network = json.loads(args.network_json.read_text(encoding="utf-8")) if args.network_json else discover_network()
    plan = stage_transition(args.state, args.stage_dir, network)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
