"""
비밀번호 해시(bcrypt) + JWT(HS256) + 라이선스 서명(HMAC).

시크릿:
- CONTROL_JWT_SECRET : 액세스 토큰 서명용
- CONTROL_LICENSE_SECRET : 오프라인 유예용 라이선스 블롭 서명용
CONTROL_ENV=development에서만 미설정 시 개발용 고정값을 허용한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any, Optional

import bcrypt
import jwt

_DEVELOPMENT = os.environ.get("CONTROL_ENV", "").strip().lower() == "development"


def _secret(name: str, development_default: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    if not _DEVELOPMENT:
        raise RuntimeError(f"{name} must be set outside development mode")
    print(
        f"WARNING: {name} is unset; using insecure development secret",
        file=sys.stderr,
    )
    return development_default


JWT_SECRET = _secret("CONTROL_JWT_SECRET", "dev-jwt-secret-change-me")
LICENSE_SECRET = _secret("CONTROL_LICENSE_SECRET", "dev-license-secret-change-me")
JWT_ALGO = "HS256"
TOKEN_TTL_SEC = 60 * 60 * 24 * 14  # 14일


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), pw_hash.encode("ascii"))
    except ValueError:
        return False


def make_token(user_id: str, email: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "email": email, "iat": now, "exp": now + TOKEN_TTL_SEC}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


def sign_license(license_obj: dict[str, Any]) -> dict[str, Any]:
    """오프라인 유예용: 라이선스 + 서명 + 서명시각. 클라이언트가 저장해두고
    오프라인일 때 이 블롭을 신뢰한다 (유예 기간 내)."""
    body = {"license": license_obj, "signed_at": int(time.time())}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(LICENSE_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return {**body, "signature": sig}


def verify_license_blob(blob: dict[str, Any]) -> bool:
    try:
        body = {"license": blob["license"], "signed_at": blob["signed_at"]}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = hmac.new(LICENSE_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, blob.get("signature", ""))
    except (KeyError, TypeError):
        return False
