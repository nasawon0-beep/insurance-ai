"""
필드 암호화 (AES-256-GCM) + 키 보관 (OS 키체인).

아키텍처 2번: "SQLite, AES-256 암호화, OS 키체인 연동".
SQLCipher 휠이 이 환경에 없어서 DB 파일 통짜 암호화 대신, 민감 필드를
값 단위로 AES-256-GCM 암호화해서 같은 TEXT 컬럼에 저장한다.

키 우선순위:
  1) 환경변수 CUSTOMER_DB_KEY_B64 (테스트/CI 용, base64 32바이트)
  2) OS 키체인 (keyring) — macOS Keychain
  3) 로컬 키파일 database/data/.dbkey (0600) — 키체인 불가 시 폴백
없으면 새로 생성해서 위 순서로 저장한다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SERVICE = "insurance-ai-local-engine"
_ACCOUNT = "customer-db-key"
_DEFAULT_KEYFILE = (
    Path.home() / ".insurance-ai" / "customer-db.key"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent / "data" / ".dbkey"
)
_KEYFILE_FALLBACK = Path(os.environ.get("CUSTOMER_DB_KEYFILE") or _DEFAULT_KEYFILE)
_PREFIX = "enc:v1:"  # 암호문 마커. 없으면 평문(구데이터)로 간주하고 그대로 반환.


def is_encrypted(value: Optional[str]) -> bool:
    return value is not None and value.startswith(_PREFIX)


def _keyring_get() -> Optional[bytes]:
    try:
        import keyring

        val = keyring.get_password(_SERVICE, _ACCOUNT)
        return base64.b64decode(val) if val else None
    except Exception:
        return None


def _keyring_set(key: bytes) -> bool:
    try:
        import keyring

        keyring.set_password(_SERVICE, _ACCOUNT, base64.b64encode(key).decode("ascii"))
        return True
    except Exception:
        return False


def _keyfile_get() -> Optional[bytes]:
    if _KEYFILE_FALLBACK.exists():
        return base64.b64decode(_KEYFILE_FALLBACK.read_text().strip())
    return None


def _keyfile_set(key: bytes) -> None:
    _KEYFILE_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    _KEYFILE_FALLBACK.write_text(base64.b64encode(key).decode("ascii"))
    os.chmod(_KEYFILE_FALLBACK, 0o600)


def _database_has_encrypted_values() -> bool:
    """키가 없을 때만 DB를 훑어 기존 암호문의 존재를 확인한다."""
    from .db import resolve_db_path

    db_path = resolve_db_path()
    if not db_path.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (table,) in tables:
                quoted_table = '"' + table.replace('"', '""') + '"'
                columns = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                for column in columns:
                    quoted_column = '"' + column[1].replace('"', '""') + '"'
                    found = conn.execute(
                        f"SELECT 1 FROM {quoted_table} "
                        f"WHERE typeof({quoted_column}) = 'text' "
                        f"AND substr({quoted_column}, 1, 7) = ? LIMIT 1",
                        (_PREFIX,),
                    ).fetchone()
                    if found:
                        return True
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(
            "고객 DB의 암호화 키 안전 검사를 완료할 수 없어 신규 키 생성을 중단합니다."
        ) from exc
    return False


class FieldCipher:
    def __init__(self, key: bytes, key_source: str):
        if len(key) != 32:
            raise ValueError("AES-256 키는 32바이트여야 합니다.")
        self._key = key
        self._aes = AESGCM(key)
        self.key_source = key_source  # "env" | "keyring" | "keyfile"

    def blind_index(self, value: Optional[str]) -> Optional[str]:
        """검색·중복확인용 결정적 지문 (HMAC-SHA256). 복호화 불가.

        주민번호처럼 '되읽을 필요는 없지만 정확히 일치 검색은 필요한' 값에 쓴다.
        """
        if value is None:
            return None
        return hmac.new(
            self._key, b"blind-index:v1:" + value.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        if plaintext is None:
            return None
        nonce = secrets.token_bytes(12)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return _PREFIX + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, stored: Optional[str]) -> Optional[str]:
        if stored is None:
            return None
        if not is_encrypted(stored):
            return stored  # 평문(암호화 도입 전 데이터) — 그대로
        blob = base64.b64decode(stored[len(_PREFIX):])
        nonce, ct = blob[:12], blob[12:]
        return self._aes.decrypt(nonce, ct, None).decode("utf-8")


_cipher: Optional[FieldCipher] = None


def get_cipher() -> FieldCipher:
    global _cipher
    if _cipher is not None:
        return _cipher

    env_key = os.environ.get("CUSTOMER_DB_KEY_B64")
    if env_key:
        _cipher = FieldCipher(base64.b64decode(env_key), "env")
        return _cipher

    key = _keyring_get()
    source = "keyring"
    if key is None:
        key = _keyfile_get()
        source = "keyfile"
    if key is None:
        if _database_has_encrypted_values():
            raise RuntimeError(
                "고객 DB에 암호문(enc:v1:)이 있지만 기존 암호화 키를 찾을 수 없습니다. "
                "키링 또는 CUSTOMER_DB_KEYFILE을 복구해야 합니다."
            )
        key = AESGCM.generate_key(bit_length=256)
        source = "keyring" if _keyring_set(key) else "keyfile"
        if source == "keyfile":
            _keyfile_set(key)

    _cipher = FieldCipher(key, source)
    return _cipher


def write_backup_key(path: Path) -> str:
    """DB 스냅샷과 함께 보관할 복구용 키 메타 파일을 0600으로 쓴다."""
    cipher = get_cipher()
    payload = json.dumps(
        {
            "version": 1,
            "key_source": cipher.key_source,
            "key_b64": base64.b64encode(cipher._key).decode("ascii"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    os.chmod(path, 0o600)
    return cipher.key_source


def reset_cache() -> None:
    """테스트에서 키 소스를 바꾼 뒤 캐시를 비울 때."""
    global _cipher
    _cipher = None
