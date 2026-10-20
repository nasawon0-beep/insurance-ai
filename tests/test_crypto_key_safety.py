"""암호화 키 소실 시 기존 고객 DB를 새 키로 덮지 않는 회귀 테스트."""
import sqlite3
import sys
import importlib.util
import ipaddress
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization


def test_qa_keyfile_only_never_reads_or_writes_os_keyring(monkeypatch):
    from database import crypto

    class ForbiddenKeyring:
        def get_password(self, *_args):
            pytest.fail("QA keyfile-only mode must not read the production keyring")

        def set_password(self, *_args):
            pytest.fail("QA keyfile-only mode must not write the production keyring")

    monkeypatch.setenv("CUSTOMER_DB_KEYFILE_ONLY", "1")
    monkeypatch.setitem(sys.modules, "keyring", ForbiddenKeyring())
    assert crypto._keyring_get() is None
    assert crypto._keyring_set(b"x" * 32) is False


def test_qa_profile_generator_creates_matching_pinned_localhost_tls_identity(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "create_qa_profile.py"
    spec = importlib.util.spec_from_file_location("qa_profile_generator", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    config = tmp_path / "config"
    module.create_pinned_tls_identity(config, "12345678-1234-1234-1234-123456789abc")
    certificate = x509.load_pem_x509_certificate((config / module.TLS_CERT_NAME).read_bytes())
    private_key = serialization.load_pem_private_key(
        (config / module.TLS_KEY_NAME).read_bytes(), password=None
    )
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    assert certificate.public_key().public_numbers() == private_key.public_key().public_numbers()
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True


def test_encrypted_db_without_key_refuses_new_key(tmp_path, monkeypatch):
    from database import crypto

    db_path = tmp_path / "customers.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES (?)", ("enc:v1:unreadable",))
    conn.commit()
    conn.close()

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    monkeypatch.delenv("CUSTOMER_DB_KEY_B64", raising=False)
    monkeypatch.setattr(crypto, "_keyring_get", lambda: None)
    monkeypatch.setattr(crypto, "_keyfile_get", lambda: None)
    monkeypatch.setattr(
        crypto,
        "_keyring_set",
        lambda _key: pytest.fail("기존 암호문이 있으면 새 키를 저장하면 안 됩니다."),
    )
    crypto.reset_cache()
    try:
        with pytest.raises(RuntimeError, match="암호문.*기존 암호화 키"):
            crypto.get_cipher()
    finally:
        crypto.reset_cache()


def test_empty_db_without_key_allows_first_key(tmp_path, monkeypatch):
    from database import crypto

    db_path = tmp_path / "customers.sqlite3"
    sqlite3.connect(str(db_path)).close()
    saved = []
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    monkeypatch.delenv("CUSTOMER_DB_KEY_B64", raising=False)
    monkeypatch.setattr(crypto, "_keyring_get", lambda: None)
    monkeypatch.setattr(crypto, "_keyfile_get", lambda: None)
    monkeypatch.setattr(crypto, "_keyring_set", lambda _key: False)
    monkeypatch.setattr(crypto, "_keyfile_set", saved.append)
    crypto.reset_cache()
    try:
        cipher = crypto.get_cipher()
        assert cipher.key_source == "keyfile"
        assert len(saved) == 1 and len(saved[0]) == 32
    finally:
        crypto.reset_cache()


def test_encrypted_db_detection_uses_targeted_queries(tmp_path, monkeypatch):
    from database import crypto
    from database.db import connect, init_schema

    db_path = tmp_path / "customers.sqlite3"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO customers (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("c1", "enc:v1:sample", "2024-01-01", "2024-01-01"),
    )
    conn.commit()
    conn.close()

    calls = []
    real_connect = sqlite3.connect

    class CountingConnection:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            calls.append(sql)
            return self._inner.execute(sql, *args, **kwargs)

        def close(self):
            return self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: CountingConnection(real_connect(*a, **kw)))

    assert crypto._database_has_encrypted_values() is True
    assert not any("sqlite_master" in sql for sql in calls)
    assert len(calls) <= 3
