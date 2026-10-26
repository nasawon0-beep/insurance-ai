from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import ssl
import sys
import threading
import time
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local-engine"))

from lan_sync.admin import tls_rollover_ready
from lan_sync.app import create_app
from lan_sync.certificate import ensure_certificate


class SyntheticTlsServer:
    def __init__(self, app, host: str, port: int, cert: str, key: str):
        self.server = uvicorn.Server(uvicorn.Config(
            app,
            host=host,
            port=port,
            ssl_certfile=cert,
            ssl_keyfile=key,
            log_level="warning",
            proxy_headers=False,
        ))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and self.thread.is_alive() and time.time() < deadline:
            time.sleep(0.02)
        assert self.server.started
        return self

    def __exit__(self, *_):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        assert not self.thread.is_alive()


def verified_post(host: str, port: int, path: str, body: dict, expected_spki: str, token: str | None = None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(host, port, timeout=5, context=context)
    connection.connect()
    der = connection.sock.getpeercert(binary_form=True)
    assert der is not None
    certificate = x509.load_der_x509_certificate(der)
    public_key = certificate.public_key()
    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    signature_hash = certificate.signature_hash_algorithm
    assert signature_hash is not None
    public_key.verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        ec.ECDSA(signature_hash),
    )
    sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address(host) in sans.get_values_for_type(x509.IPAddress)
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert hashlib.sha256(spki).hexdigest() == expected_spki
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("POST", path, body=json.dumps(body).encode(), headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode())
    connection.close()
    return response.status, payload


def test_loopback_tls_address_change_uses_same_spki_before_sending_token(tmp_path):
    host_a, host_b, port = "127.0.0.1", "127.0.0.2", 19443
    instance_id = "0123456789abcdef0123456789abcdef"
    database = tmp_path / "synthetic.sqlite3"
    tls_dir = tmp_path / "tls"
    first = ensure_certificate(tls_dir, [host_a], valid_days=30, renew_before_days=5)
    app_a = create_app(
        database,
        enable_production_bridge=False,
        pc_instance_id=instance_id,
        tls_identity_spki_sha256=first["spki_sha256"],
    )
    code = app_a.state.sync_store.create_pairing_code("합성 PC", 60)

    with SyntheticTlsServer(app_a, host_a, port, first["cert_path"], first["key_path"]):
        status, paired = verified_post(
            host_a, port, "/v1/pair", {"code": code.code, "device_name": "합성 폰"}, first["spki_sha256"]
        )
        assert status == 200
        token = paired["token"]
        status, _ = verified_post(
            host_a,
            port,
            "/v1/tls-identity/bind",
            {"spki_sha256": first["spki_sha256"]},
            first["spki_sha256"],
            token,
        )
        assert status == 200

    assert tls_rollover_ready(database, Path(first["cert_path"]))
    rotated = ensure_certificate(
        tls_dir,
        [host_b],
        valid_days=30,
        renew_before_days=5,
        allow_address_reissue=True,
    )
    assert rotated["fingerprint"] != first["fingerprint"]
    assert rotated["spki_sha256"] == first["spki_sha256"]

    app_b = create_app(
        database,
        enable_production_bridge=False,
        pc_instance_id=instance_id,
        tls_identity_spki_sha256=rotated["spki_sha256"],
    )
    with SyntheticTlsServer(app_b, host_b, port, rotated["cert_path"], rotated["key_path"]):
        status, proof = verified_post(
            host_b, port, "/v1/continuity", {}, first["spki_sha256"], token
        )
        assert status == 200
        assert proof["device_id"] == paired["device_id"]
        assert proof["pc_instance_id"] == instance_id
