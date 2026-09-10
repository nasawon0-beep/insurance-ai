"""암호화 키 소실 시 기존 고객 DB를 새 키로 덮지 않는 회귀 테스트."""
import sqlite3

import pytest


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
