"""
고객 DB 연결 / 스키마 (database/, 아키텍처 2번).

경로 우선순위: 인자 db_path > 환경변수 CUSTOMER_DB_PATH > database/data/customers.sqlite3

주의: 지금은 평문 SQLite다. 아키텍처가 요구하는 AES-256 + OS 키체인은
      실제 고객 PII를 넣기 전에 반드시 얹어야 한다 (후속 작업). 스키마와 repo 를
      분리해 둬서 나중에 SQLCipher / 필드 암호화로 교체하기 쉽다.
"""
from __future__ import annotations

import os
import logging
import sqlite3
from pathlib import Path

_PACKAGED_DEFAULT = Path(__file__).parent / "data" / "customers.sqlite3"
logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT,
    birth_date  TEXT,
    gender      TEXT,
    email       TEXT,
    address     TEXT,
    occupation  TEXT,
    tags        TEXT,   -- 태그 (콤마로 이어 저장, 암호화)
    memo        TEXT,
    rrn         TEXT,   -- 주민등록번호: AES-256-GCM 암호문(enc:v1:). 절대 평문 저장 금지.
    rrn_hash    TEXT,   -- HMAC-SHA256 지문. 중복확인/검색용, 복호화 불가.
    customer_status      TEXT,     -- 저장 상태: 가망 / 미가입 / 해지 (암호화). '가입'은 파생값이라 저장 안 함.
    birth_date_estimated INTEGER,  -- 1이면 생년월일이 추정값(평문). 파생 아님.
    first_registered_ym  TEXT,     -- 최초 고객등록 년월 'YYYY-MM'(또는 'YYYY-MM-DD'). 암호화. 미입력 시 서버가 현재 년월로 채움.
    import_batch_id TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 주민번호 전체값 조회 접근 로그 (누가·언제·왜)
CREATE TABLE IF NOT EXISTS rrn_access_log (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id            TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    insurer       TEXT,
    product_name  TEXT,
    policy_number TEXT,
    plan_type     TEXT,
    premium       INTEGER,
    payment_cycle TEXT,
    start_date    TEXT,
    end_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'ACTIVE',
    memo          TEXT,
    document_id   TEXT,              -- 연결된 약관 PDF의 RAG doc_id (sha256). 평문.
    insured_period    TEXT,          -- 보험기간 원문 (예: '100세만기', '20년') — 암호화
    payment_period    TEXT,          -- 납입기간 원문 (예: '20년납', '전기납') — 암호화
    payment_end_date  TEXT,          -- 납입 종료일 (서버 파생) — 암호화
    end_date_derived  INTEGER,       -- 0=사용자 직접입력(자동계산 금지), 1=자동계산, NULL=미정. 평문.
    is_own            INTEGER,       -- 1=내가 가입시킨 계약 / 0=아님·미확인 / NULL=미지정. 평문 플래그.
    policyholder_name TEXT,          -- 계약자(피보험자와 다를 때) 이름 — 암호화. 비어있으면 본인계약(계약자=피보험자=customer_id).
    policyholder_rel  TEXT,          -- 계약자와 피보험자의 관계 (본인/배우자/부/모/자녀/형제자매/사업자/기타) — 암호화.
    import_batch_id TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consultations (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    consulted_at TEXT NOT NULL,
    channel      TEXT,
    title        TEXT,
    content      TEXT,
    transcript   TEXT,   -- 녹취 전사 원문 (AES-256-GCM 암호문). 녹취에서 만든 기록만.
    follow_up_at TEXT,
    follow_up_done_at TEXT,   -- 후속 연락 완료 스탬프 (평문 날짜). 비어있으면 미완료.
    import_batch_id TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batch_rrn (
    batch_id    TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    rrn_hash    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (batch_id, customer_id)
);

-- 로컬 앱 설정 (RRN 파일럿 토글 등). 순수 로컬, 평문.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

-- 사용 로그 (기능 사용량 파악용). PII 없음 — 화이트리스트된 이벤트/열거값만. 평문.
CREATE TABLE IF NOT EXISTS usage_log (
    id          TEXT PRIMARY KEY,
    event       TEXT NOT NULL,
    props       TEXT,
    device_id   TEXT,
    app_version TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
    entity TEXT NOT NULL, entity_id TEXT NOT NULL, customer_id TEXT,
    fields TEXT, created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_policies_customer ON policies(customer_id);
CREATE INDEX IF NOT EXISTS idx_consultations_customer ON consultations(customer_id);
CREATE INDEX IF NOT EXISTS idx_consultations_followup ON consultations(follow_up_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_rrn_hash ON customers(rrn_hash);
CREATE INDEX IF NOT EXISTS idx_rrn_access_customer ON rrn_access_log(customer_id);
CREATE INDEX IF NOT EXISTS idx_rrn_access_customer_time ON rrn_access_log(customer_id, accessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_event_time ON usage_log(event, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_customer ON audit_log(customer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at);
"""


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.environ.get("CUSTOMER_DB_PATH") or _PACKAGED_DEFAULT)


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: async 엔드포인트(event loop 스레드)와 sync 의존성
    # (스레드풀)이 같은 연결을 만질 수 있게. 연결은 요청마다 새로 만들고 공유하지 않음.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # 고객 삭제 시 보험계약 CASCADE
    return conn


# 나중에 추가된 컬럼 — 기존 DB에 없으면 ALTER 로 채운다 (CREATE IF NOT EXISTS 로는 안 됨).
_ADDED_COLUMNS = {
    "customers": {
        "occupation": "TEXT", "tags": "TEXT", "rrn": "TEXT", "rrn_hash": "TEXT",
        "customer_status": "TEXT", "birth_date_estimated": "INTEGER",
        "first_registered_ym": "TEXT",
        "import_batch_id": "TEXT",
    },
    "policies": {
        "document_id": "TEXT",
        "insured_period": "TEXT", "payment_period": "TEXT",
        "payment_end_date": "TEXT", "end_date_derived": "INTEGER",
        "is_own": "INTEGER",
        "policyholder_name": "TEXT", "policyholder_rel": "TEXT",
        "import_batch_id": "TEXT",
    },
    "consultations": {
        "transcript": "TEXT", "coverage_json": "TEXT", "follow_up_done_at": "TEXT",
        "import_batch_id": "TEXT",
    },
    "rrn_access_log": {
        "actor": "TEXT", "access_type": "TEXT",
    },
    "import_batch_rrn": {
        "rrn_hash": "TEXT", "created_at": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, coltype in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    conn.commit()


def _migrate_rrn_unique(conn: sqlite3.Connection) -> None:
    """기존 비고유 RRN 인덱스를 고유 인덱스로 바꾼다. 기동 시에만 호출한다."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "customers" not in tables:
        return
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(customers)")}
    if "rrn_hash" not in columns:
        return

    indexes = {r["name"]: r for r in conn.execute("PRAGMA index_list(customers)")}
    current = indexes.get("idx_customers_rrn_hash")
    if current is not None and current["unique"]:
        return

    duplicates = conn.execute(
        "SELECT rrn_hash, COUNT(*) FROM customers "
        "WHERE rrn_hash IS NOT NULL GROUP BY rrn_hash HAVING COUNT(*) > 1"
    ).fetchall()
    if duplicates:
        logger.error(
            "idx_customers_rrn_hash 고유 인덱스 생성 실패: 중복 rrn_hash %d개",
            len(duplicates),
        )
        raise sqlite3.IntegrityError(
            f"중복 rrn_hash {len(duplicates)}개로 고유 인덱스를 생성할 수 없습니다."
        )

    conn.execute("DROP INDEX IF EXISTS idx_customers_rrn_hash")
    conn.execute(
        "CREATE UNIQUE INDEX idx_customers_rrn_hash ON customers(rrn_hash)"
    )
    conn.commit()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
