"""
고객 / 보험계약 CRUD. SQL 만 여기 있고, 라우터는 이 함수들만 부른다.

민감 필드는 저장 직전 AES-256-GCM 으로 암호화하고(get_cipher), 읽을 때 복호화한다.
암호화된 컬럼은 SQL 에서 검색/정렬이 불가능하므로 목록 검색은 복호화 후
파이썬에서 필터링한다 (상담자 1인의 고객 수 규모에선 충분).
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import expiry
from . import rrn as rrn_util
from .crypto import get_cipher, is_encrypted

_CUSTOMER_FIELDS = [
    "name", "phone", "birth_date", "gender", "email", "address",
    "occupation", "tags", "memo", "rrn", "customer_status",
    "first_registered_ym",  # 최초 고객등록 년월 'YYYY-MM' — 저사양이지만 고객 필드는 전부 암호화라 여기도 암호화.
]
# birth_date_estimated 는 평문 INTEGER — 필드 목록엔 안 넣고 create/update 에서 전용 처리.
_CUSTOMER_COLS = [
    "id", *_CUSTOMER_FIELDS, "birth_date_estimated", "rrn_hash",
    "created_at", "updated_at",
]
_CUSTOMER_ENC = set(_CUSTOMER_FIELDS)  # rrn/customer_status 포함 전부 암호화

_POLICY_FIELDS = [
    "insurer", "product_name", "policy_number", "plan_type", "premium",
    "payment_cycle", "start_date", "end_date", "status", "memo", "document_id",
    "insured_period", "payment_period", "payment_end_date",
    # 계약자(피보험자와 다를 때). customer_id 는 피보험자다. policyholder_name 이
    # 비어있으면 본인계약(계약자=피보험자), 값이 있으면 그 사람이 계약자.
    "policyholder_name", "policyholder_rel",
]
# end_date_derived / is_own 은 평문 INTEGER — 필드 목록엔 안 넣고 전용 처리.
_POLICY_COLS = [
    "id", "customer_id", *_POLICY_FIELDS, "end_date_derived", "is_own",
    "created_at", "updated_at",
]
_POLICY_ENC = {
    "insurer", "product_name", "policy_number", "plan_type",
    "payment_cycle", "start_date", "end_date", "memo",
    "insured_period", "payment_period", "payment_end_date",
    "policyholder_name", "policyholder_rel",
}  # premium(정수), status(코드값), document_id(해시), end_date_derived(플래그)는 평문

_CONSULT_FIELDS = [
    "consulted_at", "channel", "title", "content", "transcript", "follow_up_at",
    "coverage_json",  # 보장분석서에서 뽑은 보장현황 표 (JSON 문자열, 암호화)
    "follow_up_done_at",  # 후속 연락 완료 스탬프 (평문 날짜)
]
_CONSULT_COLS = ["id", "customer_id", *_CONSULT_FIELDS, "created_at", "updated_at"]
_CONSULT_ENC = {"channel", "title", "content", "transcript", "coverage_json"}  # 날짜는 정렬/필터 위해 평문

_KEEP = object()  # "이 컬럼은 건드리지 마라" 표식
_PHONE_QUERY_CHARS = set("0123456789 -()+.")
_DONE_MARKER_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}\] 후속 연락 완료[ \t]*(?:\r?\n|$)", re.MULTILINE
)


def _strip_done_markers(text: str) -> str:
    if not _DONE_MARKER_RE.search(text):
        return text
    return _DONE_MARKER_RE.sub("", text)


class DuplicateRRNError(Exception):
    pass


def _is_rrn_unique_error(exc: sqlite3.IntegrityError) -> bool:
    return "customers.rrn_hash" in str(exc) or "idx_customers_rrn_hash" in str(exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def log_audit(conn, *, actor, action, entity, entity_id, customer_id=None, fields=None):
    conn.execute(
        "INSERT INTO audit_log (id, actor, action, entity, entity_id, customer_id, fields, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_new_id(), actor or "unknown", action, entity, entity_id, customer_id, fields, _now()),
    )
    conn.commit()


def list_audit(conn, customer_id=None, limit=200):
    where, params = ("WHERE customer_id = ?", [customer_id]) if customer_id else ("", [])
    rows = conn.execute(
        f"SELECT id, actor, action, entity, entity_id, customer_id, fields, created_at "
        f"FROM audit_log {where} ORDER BY created_at DESC LIMIT ?", [*params, limit]
    ).fetchall()
    return [dict(r) for r in rows]


def audit_stats(conn):
    row = conn.execute("SELECT COUNT(*) c, MIN(created_at) o, MAX(created_at) n FROM audit_log").fetchone()
    return {"count": row["c"], "oldest": row["o"], "newest": row["n"]}


def prune_audit(conn, days=730):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def stamp_import_batch(
    conn: sqlite3.Connection, table: str, row_id: str, batch_id: str
) -> None:
    if table not in {"customers", "policies", "consultations"}:
        raise ValueError("unsupported import batch table")
    conn.execute(
        f"UPDATE {table} SET import_batch_id = ? WHERE id = ? AND import_batch_id IS NULL",
        (batch_id, row_id),
    )
    conn.commit()


def batch_row_ids(conn: sqlite3.Connection, batch_id: str) -> dict:
    return {
        table: [r["id"] for r in conn.execute(
            f"SELECT id FROM {table} WHERE import_batch_id = ?", (batch_id,)
        ).fetchall()]
        for table in ("customers", "policies", "consultations")
    }


def recent_batches(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT import_batch_id, id, 'customers' AS kind, created_at FROM customers "
        "WHERE import_batch_id IS NOT NULL UNION ALL "
        "SELECT import_batch_id, id, 'policies' AS kind, created_at FROM policies "
        "WHERE import_batch_id IS NOT NULL UNION ALL "
        "SELECT import_batch_id, id, 'consultations' AS kind, created_at FROM consultations "
        "WHERE import_batch_id IS NOT NULL UNION ALL "
        "SELECT batch_id AS import_batch_id, customer_id AS id, 'rrn' AS kind, created_at "
        "FROM import_batch_rrn"
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        bid = row["import_batch_id"]
        item = grouped.setdefault(bid, {
            "id": bid,
            "created_at": row["created_at"],
            "counts": {"customers": 0, "policies": 0, "consultations": 0},
        })
        item["created_at"] = min(item["created_at"], row["created_at"])
        if row["kind"] != "rrn":
            item["counts"][row["kind"]] += 1
    return sorted(grouped.values(), key=lambda x: x["created_at"], reverse=True)[:limit]


def rrn_batch_targets(conn: sqlite3.Connection, batch_id: str) -> list[tuple[str, str]]:
    return [(r["customer_id"], r["rrn_hash"]) for r in conn.execute(
        "SELECT customer_id, rrn_hash FROM import_batch_rrn WHERE batch_id = ?", (batch_id,)
    ).fetchall()]


def mark_batch_rrn(
    conn: sqlite3.Connection, batch_id: str, customer_id: str,
    rrn_hash: str, created_at: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO import_batch_rrn "
        "(batch_id, customer_id, rrn_hash, created_at) VALUES (?, ?, ?, ?)",
        (batch_id, customer_id, rrn_hash, created_at),
    )
    conn.commit()


def clear_customer_rrn(conn: sqlite3.Connection, customer_id: str, rrn_hash: str) -> int:
    cur = conn.execute(
        "UPDATE customers SET rrn = NULL, rrn_hash = NULL, updated_at = ? "
        "WHERE id = ? AND rrn_hash = ?",
        (_now(), customer_id, rrn_hash),
    )
    conn.commit()
    return cur.rowcount


def _encrypt(value, field: str, enc_fields: set) -> object:
    return get_cipher().encrypt(value) if field in enc_fields else value


_ENC_TABLES = {"customers", "policies", "consultations"}


def _decrypt_row(
    conn: sqlite3.Connection, table: str, row: sqlite3.Row, enc_fields: set
) -> dict:
    """복호화하면서, enc:v1 표식이 없는 평문(암호화 도입 전) 값을 그 자리에서 재암호화한다.

    부작용: 평문을 만나면 request conn 에서 UPDATE+commit 한다(읽기 경로의 자가치유).
    갱신은 필드별 CAS(`WHERE {k} = <읽은 평문값>`) — 그 사이 다른 요청이 같은 필드를
    바꿨으면 이 repair 는 무시되고(rowcount 0) 다음 읽기에서 다시 시도된다. 동시 PATCH 를
    덮어쓰지 않는다. table/컬럼명은 코드 상수(_ENC_TABLES · enc_fields)에서만 온다.
    """
    if table not in _ENC_TABLES:
        raise ValueError(f"unknown table: {table}")
    c = get_cipher()
    out = dict(row)
    repairs = {}  # field -> (원래 저장된 평문값, 새 암호문)
    for k in enc_fields:
        if k in out:
            if out[k] is not None and not is_encrypted(out[k]):
                repairs[k] = (out[k], c.encrypt(out[k]))
            out[k] = c.decrypt(out[k])
    if repairs:
        for k, (old_plain, new_ct) in repairs.items():
            conn.execute(
                f"UPDATE {table} SET {k} = ? WHERE id = ? AND {k} = ?",
                (new_ct, out["id"], old_plain),
            )
        conn.commit()
    return out


def count_plaintext_values(conn: sqlite3.Connection) -> int:
    """암호화 대상 컬럼 중 아직 enc:v1 표식이 없는 비-NULL 값 수 (읽기 시 자동 재암호화 대상)."""
    total = 0
    for table, fields in (
        ("customers", _CUSTOMER_ENC),
        ("policies", _POLICY_ENC),
        ("consultations", _CONSULT_ENC),
    ):
        expressions = [
            f"CASE WHEN {field} IS NOT NULL AND substr({field}, 1, 7) != 'enc:v1:' "
            "THEN 1 ELSE 0 END"
            for field in fields
        ]
        total += conn.execute(
            f"SELECT COALESCE(SUM({' + '.join(expressions)}), 0) FROM {table}"
        ).fetchone()[0]
    return total


def _coerce_is_own(p: dict) -> dict:
    """정책 dict 의 평문 INTEGER is_own(0/1/None) 을 공개 형태의 bool/None 로."""
    if "is_own" in p:
        p["is_own"] = None if p["is_own"] is None else bool(p["is_own"])
    return p


# ---------- customers ----------

def _norm_tags(v) -> Optional[str]:
    """list[str] 또는 콤마 문자열 → 정규화된 콤마 문자열 (중복 제거, 순서 유지)."""
    if v is None:
        return None
    parts = v if isinstance(v, list) else str(v).split(",")
    parts = [str(t).strip() for t in parts if str(t).strip()]
    return ",".join(dict.fromkeys(parts)) or None


def _birthday_view(birth_date: Optional[str], today: Optional[date] = None) -> Optional[dict]:
    """생년월일 → {next: 다음 생일 date, days_until, turning_age, mmdd}. 형식 이상이면 None.
    연말 wrap-around 처리, 2/29 생일이 평년이면 2/28 로."""
    today = today or date.today()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(birth_date or ""))
    if not m:
        return None
    by, bm, bd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= bm <= 12 and 1 <= bd <= 31):
        return None

    def _bday(year: int) -> Optional[date]:
        d = bd
        if bm == 2 and bd == 29 and not expiry._is_leap(year):
            d = 28
        try:
            return date(year, bm, d)
        except ValueError:
            return None

    nb = _bday(today.year)
    if nb is None:
        return None
    if nb < today:
        nb = _bday(today.year + 1)
        if nb is None:
            return None
    return {
        "next": nb,
        "days_until": (nb - today).days,
        "turning_age": nb.year - by,
        "mmdd": f"{bm:02d}-{bd:02d}",
    }


def _public_customer(d: Optional[dict], with_rrn: bool = True) -> Optional[dict]:
    """외부로 나가는 형태: rrn_hash(지문) 만 제거. 주민번호는 마스킹 없이 노출(사용자 요청).
    저장은 여전히 AES-256-GCM 암호화. 목록 응답은 with_rrn=False 로 전체값을 빼고 has_rrn 만 준다."""
    if d is None:
        return None
    out = dict(d)
    out.pop("rrn_hash", None)
    full_rrn = out.get("rrn") or None
    out["has_rrn"] = bool(full_rrn)
    out["rrn"] = full_rrn if with_rrn else None
    # 마스킹값(생년월일6+성별자리)도 PII — 목록/매칭 응답(with_rrn=False)엔 싣지 않는다.
    out["rrn_masked"] = rrn_util.mask(full_rrn) if with_rrn else None
    out["tags"] = [t for t in (out.get("tags") or "").split(",") if t]
    out["birth_date_estimated"] = bool(out.get("birth_date_estimated"))
    bv = _birthday_view(out.get("birth_date"))
    out["next_birthday"] = bv["mmdd"] if bv else None
    return out


def _enrich_customer(conn: sqlite3.Connection, pub: dict) -> dict:
    """목록 표시용 파생 정보: 계약 수 / 임박 만기 / 다음 후속 연락 / 최근 상담일."""
    cid = pub["id"]
    prows = conn.execute(
        "SELECT id, status, end_date, is_own FROM policies WHERE customer_id = ?", (cid,)
    ).fetchall()
    active_ends = []
    for r in prows:
        if r["status"] == "ACTIVE" and r["end_date"]:
            raw_end = r["end_date"]
            if not is_encrypted(raw_end):  # 평문 end_date 는 그 자리에서 재암호화 (CAS)
                conn.execute(
                    "UPDATE policies SET end_date = ? WHERE id = ? AND end_date = ?",
                    (get_cipher().encrypt(raw_end), r["id"], raw_end),
                )
                conn.commit()
            dec = get_cipher().decrypt(raw_end)
            if dec and dec.strip():
                active_ends.append(dec.strip())
    pub["policy_count"] = len(prows)
    pub["active_policy_count"] = sum(1 for r in prows if r["status"] == "ACTIVE")
    pub["own_policy_count"] = sum(1 for r in prows if r["is_own"])  # "내가 가입시킴" 계약 수
    pub["soonest_expiry"] = min(active_ends) if active_ends else None

    krows = conn.execute(
        "SELECT consulted_at, follow_up_at, follow_up_done_at "
        "FROM consultations WHERE customer_id = ?",
        (cid,),
    ).fetchall()
    today = date.today().isoformat()
    consulted = [r["consulted_at"] for r in krows if r["consulted_at"]]
    fups = [
        r["follow_up_at"] for r in krows
        if (r["follow_up_at"] or "") >= today and not (r["follow_up_done_at"] or "")
    ]
    pub["last_consulted_at"] = max(consulted) if consulted else None
    pub["next_follow_up"] = min(fups) if fups else None

    # 상태는 사용자가 직접 지정(가입/미가입/가망/해지). 계약 추가·전계약해지 시 자동 보정됨.
    pub["effective_status"] = pub.get("customer_status") or "미가입"
    return pub


def find_customers_by_phone(conn: sqlite3.Connection, phone_digits: str) -> list[dict]:
    """전화번호(숫자만) 완전일치 고객. phone 이 암호화라 복호화 후 대조."""
    if not phone_digits:
        return []
    out = []
    for r in conn.execute("SELECT * FROM customers").fetchall():
        c = _public_customer(_decrypt_row(conn, "customers", r, _CUSTOMER_ENC), with_rrn=False)
        cp = "".join(ch for ch in (c.get("phone") or "") if ch.isdigit())
        if cp and cp == phone_digits:
            out.append(_enrich_customer(conn, c))
    return out


def find_customers_by_name(conn: sqlite3.Connection, name: str) -> list[dict]:
    """이름 완전일치 고객 전부 (동명이인 판별용)."""
    if not name or not name.strip():
        return []
    target = name.strip()
    out = []
    for r in conn.execute("SELECT * FROM customers").fetchall():
        c = _public_customer(_decrypt_row(conn, "customers", r, _CUSTOMER_ENC), with_rrn=False)
        if (c.get("name") or "").strip() == target:
            out.append(_enrich_customer(conn, c))
    return out


def match_customer(
    conn: sqlite3.Connection,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    birth_date: Optional[str] = None,
) -> dict:
    """중복 고객 탐지. 우선순위: 전화 완전일치 > 이름+생년월일 > 이름만.

    returns {"match": <public customer or None>, "match_reason": str|None, "candidates": [...]}
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    phone_hits = find_customers_by_phone(conn, digits) if digits else []
    if phone_hits:
        return {"match": phone_hits[0], "match_reason": "phone", "candidates": phone_hits}

    if name and name.strip():
        name_hits = find_customers_by_name(conn, name)
        bd = (birth_date or "").strip()
        bd_hit = next(
            (c for c in name_hits if bd and (c.get("birth_date") or "") == bd), None
        )
        if bd_hit:
            return {"match": bd_hit, "match_reason": "name+birthdate", "candidates": name_hits}
        if name_hits:
            return {"match": name_hits[0], "match_reason": "name", "candidates": name_hits}

    return {"match": None, "match_reason": None, "candidates": []}


def all_tags(conn: sqlite3.Connection) -> list[str]:
    seen: set[str] = set()
    # SELECT * + _decrypt_row: 이 경로만 타는 흐름에서도 tags 평문이 재암호화되도록.
    for r in conn.execute("SELECT * FROM customers").fetchall():
        row = _decrypt_row(conn, "customers", r, _CUSTOMER_ENC)
        for t in (row.get("tags") or "").split(","):
            if t.strip():
                seen.add(t.strip())
    return sorted(seen)


def _get_customer_raw(conn: sqlite3.Connection, cid: str) -> Optional[dict]:
    """내부용: 복호화된 전체값(주민번호 원문 포함). 외부로 그대로 내보내지 말 것."""
    r = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
    return _decrypt_row(conn, "customers", r, _CUSTOMER_ENC) if r else None


def _fill_rrn_derived(data: dict) -> None:
    """주민번호가 있고 호출자가 생년월일/성별을 안 보냈으면 주민번호에서 채운다.
    주민번호 앞 6자리 + 성별자리가 생년월일·성별의 확정 근거이므로 이게 정답이다."""
    if not data.get("rrn"):
        return
    birth, gender = rrn_util.birth_and_gender(data["rrn"])
    if birth and not data.get("birth_date"):
        data["birth_date"] = birth
        data["birth_date_estimated"] = False  # 주민번호 유래 = 확정
    if gender and not data.get("gender"):
        data["gender"] = gender


def create_customer(conn: sqlite3.Connection, data: dict) -> dict:
    cid, now = _new_id(), _now()
    data = {**data, "tags": _norm_tags(data.get("tags"))}
    _fill_rrn_derived(data)
    if not data.get("customer_status"):
        data["customer_status"] = "가망"  # 신규 고객 기본
    # 최초 고객등록 년월: 미입력이면 현재 년월(UTC)로 채운다 → 모든 신규 고객이 값을 가짐. 이후 수정 가능.
    frym = (data.get("first_registered_ym") or "").strip()
    data["first_registered_ym"] = frym or datetime.now(timezone.utc).strftime("%Y-%m")
    values = {c: None for c in _CUSTOMER_COLS}
    for f in _CUSTOMER_FIELDS:
        values[f] = _encrypt(data.get(f), f, _CUSTOMER_ENC)
    values["rrn_hash"] = get_cipher().blind_index(data.get("rrn"))
    bde = data.get("birth_date_estimated")
    values["birth_date_estimated"] = int(bool(bde)) if bde is not None else None
    values.update(id=cid, created_at=now, updated_at=now)
    try:
        conn.execute(
            f"INSERT INTO customers ({', '.join(_CUSTOMER_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _CUSTOMER_COLS)})",
            [values[c] for c in _CUSTOMER_COLS],
        )
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if _is_rrn_unique_error(exc):
            raise DuplicateRRNError from exc
        raise
    conn.commit()
    return get_customer(conn, cid)  # type: ignore[return-value]


def get_customer(conn: sqlite3.Connection, cid: str) -> Optional[dict]:
    return _public_customer(_get_customer_raw(conn, cid))


def list_customers(
    conn: sqlite3.Connection,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort: str = "name",
    tag: Optional[str] = None,
    expiring_days: Optional[int] = None,
    has_follow_up: bool = False,
    status: Optional[str] = None,
    own: Optional[int] = None,
) -> list[dict]:
    rows = conn.execute("SELECT * FROM customers").fetchall()
    items = [
        _enrich_customer(conn, _public_customer(_decrypt_row(conn, "customers", r, _CUSTOMER_ENC), with_rrn=False))
        for r in rows
    ]

    if q:
        needle = q.strip().lower()
        needle_digits = "".join(ch for ch in needle if ch.isdigit())
        phone_query = (
            len(needle_digits) >= 3
            and all(ch in _PHONE_QUERY_CHARS for ch in q.strip())
        )
        policy_text: dict[str, str] = {}
        for r in conn.execute("SELECT * FROM policies").fetchall():
            p = _decrypt_row(conn, "policies", r, _POLICY_ENC)
            text = " ".join(filter(None, [
                p.get("insurer"), p.get("product_name"), p.get("policy_number"), p.get("plan_type"),
            ])).lower()
            policy_text[p["customer_id"]] = " ".join(filter(None, [policy_text.get(p["customer_id"]), text]))
        items = [
            c for c in items
            if needle in (c.get("name") or "").lower()
            or needle in (c.get("email") or "").lower()
            or needle in (c.get("address") or "").lower()
            or needle in (c.get("memo") or "").lower()
            or needle in (c.get("occupation") or "").lower()
            or any(needle in str(t).lower() for t in (c.get("tags") or []))
            or (phone_query and needle_digits in "".join(ch for ch in (c.get("phone") or "") if ch.isdigit()))
            or needle in policy_text.get(c["id"], "")
        ]
    if tag:
        items = [c for c in items if tag in c["tags"]]
    if has_follow_up:
        items = [c for c in items if c["next_follow_up"]]
    if status:
        items = [c for c in items if c.get("effective_status") == status]
    if own == 1:  # "내 계약만 보기": is_own 계약이 1건 이상인 고객만
        items = [c for c in items if c.get("own_policy_count")]
    if expiring_days is not None:
        cutoff = (date.today() + timedelta(days=expiring_days)).isoformat()
        items = [c for c in items if c["soonest_expiry"] and c["soonest_expiry"] <= cutoff]

    _FAR = "9999-99-99"
    if sort == "recent":
        items.sort(key=lambda c: c["last_consulted_at"] or "", reverse=True)
    elif sort == "expiry":
        items.sort(key=lambda c: c["soonest_expiry"] or _FAR)
    elif sort == "follow_up":
        items.sort(key=lambda c: c["next_follow_up"] or _FAR)
    elif sort == "birthday":
        items.sort(key=lambda c: (
            _birthday_view(c.get("birth_date"))["days_until"]
            if _birthday_view(c.get("birth_date")) else 10_000
        ))
    else:
        items.sort(key=lambda c: c.get("name") or "")

    return items[offset : offset + limit]


def update_customer(conn: sqlite3.Connection, cid: str, patch: dict) -> Optional[dict]:
    if _get_customer_raw(conn, cid) is None:
        return None
    patch = dict(patch)
    _fill_rrn_derived(patch)  # rrn 만 보낸 PATCH 여도 생년월일·성별을 주민번호에 맞춘다
    fields = {k: v for k, v in patch.items() if k in _CUSTOMER_FIELDS}
    if "tags" in fields:
        fields["tags"] = _norm_tags(fields["tags"])
    if "first_registered_ym" in fields and fields["first_registered_ym"] is not None:
        fields["first_registered_ym"] = str(fields["first_registered_ym"]).strip() or None
    enc = {k: _encrypt(v, k, _CUSTOMER_ENC) for k, v in fields.items()}
    if "rrn" in fields:  # 주민번호가 바뀌면 지문도 다시 계산
        enc["rrn_hash"] = get_cipher().blind_index(fields["rrn"])
    if "birth_date_estimated" in patch:  # 평문 INTEGER — 전용 처리
        v = patch["birth_date_estimated"]
        enc["birth_date_estimated"] = int(bool(v)) if v is not None else None
    if enc:
        enc["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in enc)
        try:
            conn.execute(f"UPDATE customers SET {sets} WHERE id = ?", [*enc.values(), cid])
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            if _is_rrn_unique_error(exc):
                raise DuplicateRRNError from exc
            raise
        conn.commit()
    if "birth_date" in fields:  # 생년월일 바뀌면 N세형·자동계산 계약 만기 재산출
        _recompute_customer_policy_expiry(conn, cid)
    return get_customer(conn, cid)


def delete_customer(conn: sqlite3.Connection, cid: str) -> bool:
    cur = conn.execute("DELETE FROM customers WHERE id = ?", (cid,))
    conn.execute("DELETE FROM rrn_access_log WHERE customer_id = ?", (cid,))
    conn.commit()
    return cur.rowcount > 0


def customer_detail(conn: sqlite3.Connection, cid: str) -> Optional[dict]:
    c = get_customer(conn, cid)
    if c is None:
        return None
    c["policies"] = list_policies(conn, cid)
    c["consultations"] = list_consultations(conn, cid)
    active = sum(1 for p in c["policies"] if p.get("status") == "ACTIVE")
    c["policy_count"] = len(c["policies"])
    c["active_policy_count"] = active
    c["effective_status"] = c.get("customer_status") or "미가입"
    return c


def find_customer_id_by_rrn(
    conn: sqlite3.Connection, rrn_norm: Optional[str], exclude_id: Optional[str] = None
) -> Optional[str]:
    """정규화된 주민번호로 기존 고객 찾기 (중복 방지용). 지문 대조라 복호화 안 함."""
    if not rrn_norm:
        return None
    h = get_cipher().blind_index(rrn_norm)
    if exclude_id is None:
        r = conn.execute("SELECT id FROM customers WHERE rrn_hash = ?", (h,)).fetchone()
    else:
        r = conn.execute(
            "SELECT id FROM customers WHERE rrn_hash = ? AND id != ?", (h, exclude_id)
        ).fetchone()
    return r[0] if r else None


def log_rrn_access(
    conn: sqlite3.Connection, cid: str, purpose: str, actor: str, access_type: str
) -> None:
    conn.execute(
        "INSERT INTO rrn_access_log "
        "(id, customer_id, purpose, accessed_at, actor, access_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_new_id(), cid, purpose, _now(), actor, access_type),
    )
    conn.commit()


def log_rrn_access_many(
    conn: sqlite3.Connection,
    customer_ids: list[str],
    purpose: str,
    actor: str,
    access_type: str,
) -> None:
    now = _now()
    conn.executemany(
        "INSERT INTO rrn_access_log "
        "(id, customer_id, purpose, accessed_at, actor, access_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(_new_id(), cid, purpose, now, actor, access_type) for cid in customer_ids],
    )
    conn.commit()


def reveal_rrn(
    conn: sqlite3.Connection, cid: str, purpose: str, actor: str = "unknown"
) -> Optional[dict]:
    """주민번호 전체값을 돌려주고 접근 로그를 남긴다. 고객/주민번호 없으면 None."""
    raw = _get_customer_raw(conn, cid)
    if raw is None or not raw.get("rrn"):
        return None
    log_rrn_access(conn, cid, purpose, actor, "explicit_reveal")
    return {"rrn": raw["rrn"], "rrn_masked": rrn_util.mask(raw["rrn"])}


def list_rrn_access(conn: sqlite3.Connection, cid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, customer_id, purpose, accessed_at, actor, access_type "
        "FROM rrn_access_log "
        "WHERE customer_id = ? ORDER BY accessed_at DESC",
        (cid,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- policies ----------

def _write_policy_fields(
    conn: sqlite3.Connection, pid: str, fields: dict, set_derived=_KEEP, set_own=_KEEP
) -> None:
    """policies 부분 업데이트. fields 는 평문 값(암호화는 여기서). set_derived 가
    _KEEP 이 아니면 end_date_derived 컬럼도 그 값(0/1/None)으로 쓴다.
    set_own 이 _KEEP 이 아니면 is_own 컬럼(평문 0/1/None)도 그 값으로 쓴다."""
    enc = {k: _encrypt(v, k, _POLICY_ENC) for k, v in fields.items() if k in _POLICY_FIELDS}
    if set_derived is not _KEEP:
        enc["end_date_derived"] = set_derived
    if set_own is not _KEEP:
        enc["is_own"] = set_own
    if not enc:
        return
    enc["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in enc)
    conn.execute(f"UPDATE policies SET {sets} WHERE id = ?", [*enc.values(), pid])
    conn.commit()


def _customer_birth(conn: sqlite3.Connection, customer_id: str) -> Optional[str]:
    raw = _get_customer_raw(conn, customer_id)
    return raw.get("birth_date") if raw else None


def create_policy(
    conn: sqlite3.Connection, customer_id: str, data: dict
) -> Optional[dict]:
    if get_customer(conn, customer_id) is None:
        return None
    pid, now = _new_id(), _now()
    data = dict(data)

    # --- 만기 / 납입종료일 자동 산출 ---
    birth = _customer_birth(conn, customer_id)
    start = (data.get("start_date") or "").strip() or None
    explicit_end = (data.get("end_date") or "").strip() or None
    if explicit_end:
        data["end_date"] = explicit_end
        end_derived = 0  # 사용자 직접입력 → 이후 자동계산 금지
    else:
        ed, _warn = expiry.compute_end_date(
            data.get("insured_period"), birth, start, start
        )
        data["end_date"] = ed
        end_derived = 1 if ed else None
    data["payment_end_date"] = expiry.compute_payment_end_date(
        data.get("payment_period"), birth, start, data.get("end_date")
    )

    # "내가 가입시킴" 플래그: 호출자가 안 주면 기본 1(ON) — 상담자가 입력하는 계약 대부분이 본인 판매분.
    is_own_val = 1 if data.get("is_own") is None else int(bool(data["is_own"]))

    values = {c: None for c in _POLICY_COLS}
    for f in _POLICY_FIELDS:
        values[f] = _encrypt(data.get(f), f, _POLICY_ENC)
    values["end_date_derived"] = end_derived
    values["is_own"] = is_own_val
    values.update(
        id=pid,
        customer_id=customer_id,
        status=data.get("status") or "ACTIVE",
        created_at=now,
        updated_at=now,
    )
    conn.execute(
        f"INSERT INTO policies ({', '.join(_POLICY_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _POLICY_COLS)})",
        [values[c] for c in _POLICY_COLS],
    )
    conn.commit()
    # ACTIVE 계약을 새로 넣으면 고객을 '가입'으로 올린다 (이미 '가입'이면 그대로).
    if (data.get("status") or "ACTIVE") == "ACTIVE":
        raw = _get_customer_raw(conn, customer_id)
        if raw and (raw.get("customer_status") or "") != "가입":
            conn.execute(
                "UPDATE customers SET customer_status = ?, updated_at = ? WHERE id = ?",
                (get_cipher().encrypt("가입"), _now(), customer_id),
            )
            conn.commit()
    return get_policy(conn, pid)


def get_policy(conn: sqlite3.Connection, pid: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM policies WHERE id = ?", (pid,)).fetchone()
    return _coerce_is_own(_decrypt_row(conn, "policies", r, _POLICY_ENC)) if r else None


def list_policies(conn: sqlite3.Connection, customer_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM policies WHERE customer_id = ? ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return [_coerce_is_own(_decrypt_row(conn, "policies", r, _POLICY_ENC)) for r in rows]


def update_policy(conn: sqlite3.Connection, pid: str, patch: dict) -> Optional[dict]:
    cur = get_policy(conn, pid)
    if cur is None:
        return None
    fields = {k: v for k, v in patch.items() if k in _POLICY_FIELDS}
    birth = _customer_birth(conn, cur["customer_id"])
    cur_derived = cur.get("end_date_derived")

    # is_own: 평문 플래그. patch 에 있을 때만 건드린다 (명시적 None → NULL 로 되돌림).
    set_own = _KEEP
    if "is_own" in patch:
        set_own = None if patch["is_own"] is None else int(bool(patch["is_own"]))

    new_insured = fields.get("insured_period", cur.get("insured_period"))
    new_start = (fields.get("start_date", cur.get("start_date")) or "").strip() or None

    set_derived = _KEEP  # end_date_derived 를 건드릴지
    recompute = False    # insured_period 기반으로 다시 계산할지

    if "end_date" in fields:
        ev = (fields["end_date"] or "").strip() if fields["end_date"] else ""
        if ev:                       # 사용자 직접입력 → 그 값 + 자동계산 금지
            fields["end_date"] = ev
            set_derived = 0
        else:                        # ""/None → NULL 로 직접 확정하고 재계산 금지
            fields["end_date"] = None
            set_derived = 0
    else:
        # end_date_derived==0(수동)이면 자동으로 절대 안 건드린다.
        changed = (
            ("insured_period" in fields and fields["insured_period"] != cur.get("insured_period"))
            or ("start_date" in fields and (fields["start_date"] or None) != (cur.get("start_date") or None))
        )
        if changed and cur_derived != 0:
            recompute = True

    if recompute:
        ed, _warn = expiry.compute_end_date(new_insured, birth, new_start, new_start)
        fields["end_date"] = ed
        set_derived = 1 if ed else None

    # 납입종료일: end_date 를 다시 잡았거나 payment_period/start_date 가 바뀌면 재계산
    eff_end = fields["end_date"] if ("end_date" in fields) else cur.get("end_date")
    if ("end_date" in fields) or ("payment_period" in fields) or ("start_date" in fields):
        new_payment = fields.get("payment_period", cur.get("payment_period"))
        fields["payment_end_date"] = expiry.compute_payment_end_date(
            new_payment, birth, new_start, eff_end
        )

    if fields or set_derived is not _KEEP or set_own is not _KEEP:
        _write_policy_fields(conn, pid, fields, set_derived, set_own)

    # 계약 상태가 ACTIVE → 비활성으로 바뀌면 고객 상태(해지) 재평가
    if "status" in fields and cur["status"] == "ACTIVE" and fields["status"] != "ACTIVE":
        _recompute_customer_status(conn, cur["customer_id"], from_status_change=True)

    return get_policy(conn, pid)


def _recompute_customer_policy_expiry(conn: sqlite3.Connection, customer_id: str) -> None:
    """고객 생년월일이 바뀌었을 때: 자동계산(파생) 계약의 만기/납입종료일을 다시 잡는다.
    end_date_derived==0(수동)은 건너뛴다. 계산 불가 시 기존 값을 보존한다(덮어쓰지 않음)."""
    birth = _customer_birth(conn, customer_id)
    for r in conn.execute(
        "SELECT id FROM policies WHERE customer_id = ?", (customer_id,)
    ).fetchall():
        p = get_policy(conn, r["id"])
        if p.get("end_date_derived") == 0:
            continue
        parsed = expiry.parse_insured_period(p.get("insured_period"))
        if parsed is None or parsed[0] == "whole":
            continue
        ed, _warn = expiry.compute_end_date(
            p.get("insured_period"), birth, p.get("start_date"), p.get("start_date")
        )
        pe = expiry.compute_payment_end_date(
            p.get("payment_period"), birth, p.get("start_date"), ed or p.get("end_date")
        )
        upd = {}
        if ed is not None and ed != (p.get("end_date") or None):
            upd["end_date"] = ed
        if pe is not None and pe != (p.get("payment_end_date") or None):
            upd["payment_end_date"] = pe
        if upd:
            _write_policy_fields(
                conn, r["id"], upd,
                set_derived=(1 if "end_date" in upd else _KEEP),
            )


def delete_policy(conn: sqlite3.Connection, pid: str) -> bool:
    row = conn.execute(
        "SELECT customer_id FROM policies WHERE id = ?", (pid,)
    ).fetchone()
    cur = conn.execute("DELETE FROM policies WHERE id = ?", (pid,))
    conn.commit()
    if cur.rowcount > 0 and row:
        _revert_status_after_delete(conn, row["customer_id"])
    return cur.rowcount > 0


def _revert_status_after_delete(conn: sqlite3.Connection, customer_id: str) -> None:
    """계약을 지워서 계약이 하나도 안 남고, 상태가 (계약 추가로 자동으로 올라간) '가입'
    이면 '가망'으로 되돌린다. 계약이 남아 있거나 사용자가 직접 정한 다른 상태면 건드리지 않는다."""
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM policies WHERE customer_id = ?", (customer_id,)
    ).fetchone()["c"]
    if n:
        return
    raw = _get_customer_raw(conn, customer_id)
    if raw and (raw.get("customer_status") or "") == "가입":
        conn.execute(
            "UPDATE customers SET customer_status = ?, updated_at = ? WHERE id = ?",
            (get_cipher().encrypt("가망"), _now(), customer_id),
        )
        conn.commit()


def _recompute_customer_status(
    conn: sqlite3.Connection, customer_id: str, *, from_status_change: bool
) -> None:
    """활성 계약 수를 보고 필요 시 customers.customer_status='해지' 를 스탬프한다.
    from_status_change=True (계약이 ACTIVE→비활성으로 바뀐 경로)이고 활성 계약이
    0이면 '해지'로 저장한다. 삭제 경로 등 from_status_change=False 는 스탬프 안 함."""
    if not from_status_change:
        return
    rows = conn.execute(
        "SELECT status FROM policies WHERE customer_id = ?", (customer_id,)
    ).fetchall()
    active = sum(1 for r in rows if r["status"] == "ACTIVE")
    if active == 0:
        conn.execute(
            "UPDATE customers SET customer_status = ?, updated_at = ? WHERE id = ?",
            (get_cipher().encrypt("해지"), _now(), customer_id),
        )
        conn.commit()


_MEMO_INSURED_RE = re.compile(r"보험기간\s*([^\s·/]+)")   # "90세만기/30년납" → "90세만기" 까지만
_MEMO_PAYMENT_RE = re.compile(r"납입기간\s*([^\s·/]+)")


def recompute_all_expiry(conn: sqlite3.Connection) -> dict:
    """전 계약을 순회하며 보험기간 기반으로 만기/납입종료일을 채운다 (백필 엔드포인트).
    insured_period 가 비어 있으면 memo 에서 정규식으로 추출한다.
    end_date_derived==0(수동 입력)은 건너뛴다. N세형인데 생년월일이 없으면 need_birthdate.
    멱등: 값이 이미 맞으면 다시 쓰지 않는다."""
    scanned = filled_from_memo = updated = skipped_manual = 0
    need_birthdate: list[dict] = []
    failed: list[dict] = []

    for r in conn.execute("SELECT id FROM policies").fetchall():
        pid = r["id"]
        scanned += 1
        try:
            p = get_policy(conn, pid)
            if p.get("end_date_derived") == 0:
                skipped_manual += 1
                continue

            insured = p.get("insured_period")
            payment = p.get("payment_period")
            memo = p.get("memo") or ""
            new_fields: dict = {}
            if not insured:
                m = _MEMO_INSURED_RE.search(memo)
                if m:
                    insured = m.group(1)
                    new_fields["insured_period"] = insured
                    filled_from_memo += 1
            if not payment:
                m = _MEMO_PAYMENT_RE.search(memo)
                if m:
                    payment = m.group(1)
                    new_fields["payment_period"] = payment

            parsed = expiry.parse_insured_period(insured)
            raw = _get_customer_raw(conn, p["customer_id"])
            birth = raw.get("birth_date") if raw else None

            if parsed and parsed[0] == "age" and not birth:
                need_birthdate.append({
                    "policy_id": pid,
                    "customer_id": p["customer_id"],
                    "customer_name": raw.get("name") if raw else None,
                    "insured_period": insured,
                })
                if new_fields:  # memo 에서 뽑은 구조화 값은 저장해 둔다
                    _write_policy_fields(conn, pid, new_fields)
                    updated += 1
                continue

            ed, _warn = expiry.compute_end_date(
                insured, birth, p.get("start_date"), p.get("start_date")
            )
            pe = expiry.compute_payment_end_date(
                payment, birth, p.get("start_date"), ed or p.get("end_date")
            )
            if ed is not None and ed != (p.get("end_date") or None):
                new_fields["end_date"] = ed
            if pe is not None and pe != (p.get("payment_end_date") or None):
                new_fields["payment_end_date"] = pe

            if new_fields:
                _write_policy_fields(
                    conn, pid, new_fields,
                    set_derived=(1 if "end_date" in new_fields else _KEEP),
                )
                updated += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"policy_id": pid, "error": str(e)})

    # 상태 미지정인데 ACTIVE 계약이 있는 고객 → '가입' 으로 보정
    status_fixed = 0
    for r in conn.execute("SELECT id FROM customers").fetchall():
        raw = _get_customer_raw(conn, r["id"])
        if raw and (raw.get("customer_status") or "") == "":
            act = conn.execute(
                "SELECT COUNT(*) FROM policies WHERE customer_id = ? AND status = 'ACTIVE'",
                (r["id"],),
            ).fetchone()[0]
            if act > 0:
                conn.execute(
                    "UPDATE customers SET customer_status = ?, updated_at = ? WHERE id = ?",
                    (get_cipher().encrypt("가입"), _now(), r["id"]),
                )
                status_fixed += 1
    if status_fixed:
        conn.commit()

    return {
        "scanned": scanned,
        "filled_from_memo": filled_from_memo,
        "updated": updated,
        "skipped_manual": skipped_manual,
        "status_fixed": status_fixed,
        "need_birthdate": need_birthdate,
        "failed": failed,
    }


# ---------- consultations (상담 이력) ----------

def create_consultation(
    conn: sqlite3.Connection, customer_id: str, data: dict
) -> Optional[dict]:
    if get_customer(conn, customer_id) is None:
        return None
    kid, now = _new_id(), _now()
    values = {c: None for c in _CONSULT_COLS}
    for f in _CONSULT_FIELDS:
        values[f] = _encrypt(data.get(f), f, _CONSULT_ENC)
    # consulted_at 은 암호화 안 함 — 위 루프에서 평문 그대로 들어감. 비었으면 now.
    values["consulted_at"] = data.get("consulted_at") or now
    values.update(id=kid, customer_id=customer_id, created_at=now, updated_at=now)
    conn.execute(
        f"INSERT INTO consultations ({', '.join(_CONSULT_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _CONSULT_COLS)})",
        [values[c] for c in _CONSULT_COLS],
    )
    conn.commit()
    return get_consultation(conn, kid)


def get_consultation(conn: sqlite3.Connection, kid: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM consultations WHERE id = ?", (kid,)).fetchone()
    return _decrypt_row(conn, "consultations", r, _CONSULT_ENC) if r else None


def list_consultations(conn: sqlite3.Connection, customer_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM consultations WHERE customer_id = ? "
        "ORDER BY consulted_at DESC",
        (customer_id,),
    ).fetchall()
    return [_decrypt_row(conn, "consultations", r, _CONSULT_ENC) for r in rows]


def update_consultation(
    conn: sqlite3.Connection, kid: str, patch: dict
) -> Optional[dict]:
    if get_consultation(conn, kid) is None:
        return None
    fields = {k: v for k, v in patch.items() if k in _CONSULT_FIELDS}
    # 새 후속 연락 예정일이 잡히면 완료 스탬프를 지운다 (다시 '미완료'로).
    if fields.get("follow_up_at") and "follow_up_done_at" not in fields:
        fields["follow_up_done_at"] = None
    if fields:
        enc = {k: _encrypt(v, k, _CONSULT_ENC) for k, v in fields.items()}
        enc["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in enc)
        conn.execute(
            f"UPDATE consultations SET {sets} WHERE id = ?", [*enc.values(), kid]
        )
        conn.commit()
    return get_consultation(conn, kid)


def complete_follow_up(conn: sqlite3.Connection, kid: str) -> Optional[dict]:
    """후속 연락 완료: follow_up_done_at 스탬프(UTC 날짜)."""
    if get_consultation(conn, kid) is None:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute(
        "UPDATE consultations SET follow_up_done_at = ?, updated_at = ? WHERE id = ?",
        (today, _now(), kid),
    )
    conn.commit()
    return get_consultation(conn, kid)


def reopen_follow_up(conn: sqlite3.Connection, kid: str) -> Optional[dict]:
    """완료 취소: 스탬프를 지우고 구버전 완료 마커를 정리한다."""
    k = get_consultation(conn, kid)
    if k is None:
        return None
    content = k.get("content")
    cleaned = _strip_done_markers(content) if content else content
    if content is not None and cleaned != content:
        cur = conn.execute(
            "UPDATE consultations SET follow_up_done_at = NULL, content = ?, updated_at = ? "
            "WHERE id = ? AND updated_at = ?",
            (get_cipher().encrypt(cleaned), _now(), kid, k["updated_at"]),
        )
        if cur.rowcount == 0:
            conn.execute(
                "UPDATE consultations SET follow_up_done_at = NULL, updated_at = ? WHERE id = ?",
                (_now(), kid),
            )
    else:
        conn.execute(
            "UPDATE consultations SET follow_up_done_at = NULL, updated_at = ? WHERE id = ?",
            (_now(), kid),
        )
    conn.commit()
    return get_consultation(conn, kid)


def delete_consultation(conn: sqlite3.Connection, kid: str) -> bool:
    cur = conn.execute("DELETE FROM consultations WHERE id = ?", (kid,))
    conn.commit()
    return cur.rowcount > 0


def set_policy_document(
    conn: sqlite3.Connection, pid: str, document_id: Optional[str]
) -> Optional[dict]:
    """정책에 약관 문서(RAG doc_id)를 연결/해제한다."""
    if get_policy(conn, pid) is None:
        return None
    conn.execute(
        "UPDATE policies SET document_id = ?, updated_at = ? WHERE id = ?",
        (document_id, _now(), pid),
    )
    conn.commit()
    return get_policy(conn, pid)


def customer_document_ids(conn: sqlite3.Connection, customer_id: str) -> list[str]:
    """고객의 보험계약에 연결된 약관 문서 id 목록 (중복 제거)."""
    rows = conn.execute(
        "SELECT DISTINCT document_id FROM policies "
        "WHERE customer_id = ? AND document_id IS NOT NULL AND document_id != ''",
        (customer_id,),
    ).fetchall()
    return [r[0] for r in rows]


def upcoming_follow_ups(conn: sqlite3.Connection, until: str) -> list[dict]:
    """follow_up_at 이 until(포함) 이하인 상담들 — '후속 연락 예정' 목록.

    고객 이름을 붙여서 돌려준다 (대시보드용).
    """
    rows = conn.execute(
        "SELECT * FROM consultations "
        "WHERE follow_up_at IS NOT NULL AND follow_up_at != '' AND follow_up_at <= ? "
        "AND (follow_up_done_at IS NULL OR follow_up_done_at = '') "
        "ORDER BY follow_up_at",
        (until,),
    ).fetchall()
    out = []
    for r in rows:
        item = _decrypt_row(conn, "consultations", r, _CONSULT_ENC)
        cust = get_customer(conn, item["customer_id"])
        item["customer_name"] = cust["name"] if cust else None
        out.append(item)
    return out


# ---------- dashboard (홈) ----------

def _name_of(conn: sqlite3.Connection, customer_id: str) -> Optional[str]:
    c = get_customer(conn, customer_id)
    return c["name"] if c else None


def _days_between(target: str, today: Optional[date] = None) -> Optional[int]:
    today = today or date.today()
    try:
        y, m, d = int(target[:4]), int(target[5:7]), int(target[8:10])
        return (date(y, m, d) - today).days
    except (ValueError, TypeError):
        return None


def expiring_policies(
    conn: sqlite3.Connection, until: str, own: Optional[int] = None
) -> list[dict]:
    """만기일(end_date)이 until 이하인 ACTIVE 계약. end_date 가 암호화라 파이썬에서 필터.
    각 항목에 insured_period / end_date_derived / days(오늘까지 남은 일수) 포함.
    own == 1 이면 is_own(내가 가입시킴) 계약만."""
    rows = conn.execute("SELECT * FROM policies WHERE status = 'ACTIVE'").fetchall()
    out = []
    today = date.today()
    for r in rows:
        p = _coerce_is_own(_decrypt_row(conn, "policies", r, _POLICY_ENC))
        if own == 1 and not p.get("is_own"):
            continue
        end = (p.get("end_date") or "").strip()
        if end and end <= until:
            p["customer_name"] = _name_of(conn, p["customer_id"])
            p["days"] = _days_between(end, today)
            out.append(p)
    out.sort(key=lambda p: p["end_date"])
    return out


def payment_ending_policies(conn: sqlite3.Connection, until: str) -> list[dict]:
    """납입종료일(payment_end_date)이 until 이하인 ACTIVE 계약."""
    rows = conn.execute("SELECT * FROM policies WHERE status = 'ACTIVE'").fetchall()
    out = []
    today = date.today()
    for r in rows:
        p = _coerce_is_own(_decrypt_row(conn, "policies", r, _POLICY_ENC))
        pe = (p.get("payment_end_date") or "").strip()
        if pe and pe <= until:
            p["customer_name"] = _name_of(conn, p["customer_id"])
            p["days"] = _days_between(pe, today)
            out.append(p)
    out.sort(key=lambda p: p["payment_end_date"])
    return out


def upcoming_birthdays(conn: sqlite3.Connection, days: int) -> list[dict]:
    """days 일 안에 생일이 오는 고객. 연말 wrap-around, 2/29→평년 2/28, 형식 이상은 제외.
    → [{id, name, birth_date, days_until, turning_age, estimated}]."""
    today = date.today()
    out = []
    for r in conn.execute("SELECT * FROM customers").fetchall():
        c = _decrypt_row(conn, "customers", r, _CUSTOMER_ENC)
        bv = _birthday_view(c.get("birth_date"), today)
        if bv is None or bv["days_until"] > days:
            continue
        out.append({
            "id": c["id"],
            "name": c["name"],
            "birth_date": c["birth_date"],
            "days_until": bv["days_until"],
            "turning_age": bv["turning_age"],
            "estimated": bool(c.get("birth_date_estimated")),
        })
    out.sort(key=lambda x: x["days_until"])
    return out


def recent_consultations(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """최근 상담 (전체 고객). transcript 는 무거워서 뺀다.
    '최근 활동' 뷰라 후속 연락 완료 여부와 무관하게 최신순 전부 보여준다."""
    rows = conn.execute(
        "SELECT * FROM consultations ORDER BY consulted_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        item = _decrypt_row(conn, "consultations", r, _CONSULT_ENC)
        item.pop("transcript", None)
        item.pop("coverage_json", None)
        item["customer_name"] = _name_of(conn, item["customer_id"])
        out.append(item)
    return out


def dashboard_counts(conn: sqlite3.Connection, own: Optional[int] = None) -> dict:
    c = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if own == 1:
        # 계약 수만 "내가 가입시킴"으로 좁힌다. 고객 수·상담 수는 전역 유지.
        p = conn.execute("SELECT COUNT(*) FROM policies WHERE is_own = 1").fetchone()[0]
        ap = conn.execute(
            "SELECT COUNT(*) FROM policies WHERE status = 'ACTIVE' AND is_own = 1"
        ).fetchone()[0]
    else:
        p = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
        ap = conn.execute(
            "SELECT COUNT(*) FROM policies WHERE status = 'ACTIVE'"
        ).fetchone()[0]
    k = conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0]
    return {"customers": c, "policies": p, "active_policies": ap, "consultations": k}
