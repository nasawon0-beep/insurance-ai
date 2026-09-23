"""
통계 계산 및 집계 모듈.

EnhancedDashboard를 위한 통계 API 백엔드.
- 월별/분기별/연도별/전체 기간 요약 통계
- 차트 데이터 (월별 추이, 지역별, 보험사별)
- 5분 인메모리 캐싱

주의: 고객/계약/상담의 표시 필드 일부는 repo 레이어에서 AES 암호화된다.
통계 SQL은 평문 테스트 스키마와 암호화된 실제 스키마를 모두 처리하도록
행을 읽은 뒤 필요한 필드만 복호화하여 집계한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

try:
    from .crypto import get_cipher as _get_cipher, is_encrypted as _is_encrypted
except Exception:  # pragma: no cover - standalone import fallback
    _get_cipher = None  # type: ignore

    def _is_encrypted(value) -> bool:  # type: ignore
        return False

is_encrypted: Callable[[object], bool] = _is_encrypted
get_cipher = _get_cipher


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _rowdicts(conn, table: str) -> list[dict]:
    if not _table_exists(conn, table):
        return []
    cur = conn.execute(f"SELECT * FROM {table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _decrypt(value):
    if value is None or not is_encrypted(value) or get_cipher is None:
        return value
    try:
        return get_cipher().decrypt(value)
    except Exception:
        return value


def _decrypt_fields(rows: list[dict], fields: set[str]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        for field in fields:
            if field in item:
                item[field] = _decrypt(item[field])
        out.append(item)
    return out


def _customers(conn) -> list[dict]:
    return _decrypt_fields(_rowdicts(conn, "customers"), {"address"})


def _policies(conn) -> list[dict]:
    return _decrypt_fields(
        _rowdicts(conn, "policies"),
        {"insurer", "payment_cycle", "start_date", "end_date"},
    )


def _consultations(conn) -> list[dict]:
    return _decrypt_fields(
        _rowdicts(conn, "consultations"),
        {"consulted_at", "channel", "follow_up_at"},
    )


def _period_window(period: str, year: int, month: Optional[int]) -> tuple[str, str, str]:
    if period == "month":
        m = month or datetime.now().month
        start = date(year, m, 1)
        end = date(year + 1, 1, 1) if m == 12 else date(year, m + 1, 1)
        return f"{year}-{m:02d}", start.isoformat(), end.isoformat()
    if period == "quarter":
        m = month or datetime.now().month
        q = ((m - 1) // 3) + 1
        start_month = ((q - 1) * 3) + 1
        start = date(year, start_month, 1)
        end = date(year + 1, 1, 1) if q == 4 else date(year, start_month + 3, 1)
        return f"{year}-Q{q}", start.isoformat(), end.isoformat()
    if period == "year":
        return str(year), f"{year}-01-01", f"{year + 1}-01-01"
    return "all-time", "0000-01-01", "9999-12-31"


def _in_window(value, start: str, end: str) -> bool:
    if not value:
        return False
    text = str(value)[:10]
    return start <= text < end


def _active(policy: dict) -> bool:
    return (policy.get("status") or "ACTIVE") == "ACTIVE"


def _premium(policy: dict) -> int:
    try:
        return int(policy.get("premium") or 0)
    except (TypeError, ValueError):
        return 0


def _monthly_premium(policy: dict) -> int:
    amount = _premium(policy)
    cycle = policy.get("payment_cycle") or "MONTHLY"
    if cycle == "YEARLY":
        return amount // 12
    if cycle == "LUMPSUM":
        return 0
    return amount


def _region(address) -> Optional[str]:
    if not address:
        return None
    parts = str(address).strip().split()
    return parts[0] if parts else None


def calculate_summary(
    conn,
    period: str = "month",
    year: int = 2026,
    month: Optional[int] = None,
) -> dict:
    """고객/계약/상담 통계 요약."""
    period_str, date_start, date_end = _period_window(period, year, month)
    customers = _customers(conn)
    policies = _policies(conn)
    consultations = _consultations(conn)

    total_customers = len(customers)
    new_customers = sum(1 for c in customers if _in_window(c.get("created_at"), date_start, date_end))

    active_policies = [p for p in policies if _active(p)]
    customers_with_policies = len({p.get("customer_id") for p in active_policies if p.get("customer_id")})
    if customers_with_policies:
        per_customer: dict[str, int] = {}
        for p in active_policies:
            cid = p.get("customer_id")
            if cid:
                per_customer[cid] = per_customer.get(cid, 0) + 1
        avg_policies = sum(per_customer.values()) / len(per_customer)
    else:
        avg_policies = 0.0

    total_monthly_premium = sum(_monthly_premium(p) for p in active_policies)
    avg_premium = total_monthly_premium // len(active_policies) if active_policies else 0

    today = datetime.now().date().isoformat()
    after_30 = (datetime.now().date() + timedelta(days=30)).isoformat()
    expiry_30days = sum(
        1 for p in active_policies
        if p.get("end_date") and today <= str(p.get("end_date"))[:10] < after_30
    )

    consultations_this_period = [
        c for c in consultations if _in_window(c.get("consulted_at"), date_start, date_end)
    ]
    pending_followups = sum(
        1 for c in consultations
        if c.get("follow_up_at") and str(c.get("follow_up_at"))[:10] >= today
    )
    channels: dict[str, int] = {}
    for c in consultations_this_period:
        channel = c.get("channel") or "기타"
        channels[channel] = channels.get(channel, 0) + 1

    premium_by_customer: dict[str, int] = {}
    for p in active_policies:
        cid = p.get("customer_id")
        if cid:
            premium_by_customer[cid] = premium_by_customer.get(cid, 0) + _monthly_premium(p)

    regional: dict[str, dict[str, int]] = {}
    for c in customers:
        region = _region(c.get("address"))
        if not region:
            continue
        bucket = regional.setdefault(region, {"customers": 0, "total_premium": 0})
        bucket["customers"] += 1
        cid = c.get("id")
        bucket["total_premium"] += premium_by_customer.get(str(cid), 0) if cid else 0
    regional_top5 = [
        {"region": region, **values}
        for region, values in sorted(
            regional.items(), key=lambda item: item[1]["customers"], reverse=True
        )[:5]
    ]

    insurer: dict[str, dict[str, int]] = {}
    for p in active_policies:
        name = p.get("insurer")
        if not name:
            continue
        bucket = insurer.setdefault(str(name), {"policies": 0, "total_premium": 0})
        bucket["policies"] += 1
        bucket["total_premium"] += _monthly_premium(p)
    insurer_top5 = [
        {"insurer": name, **values}
        for name, values in sorted(
            insurer.items(), key=lambda item: item[1]["policies"], reverse=True
        )[:5]
    ]

    return {
        "period": period_str,
        "customers": {
            "total": total_customers,
            "new_this_period": new_customers,
            "with_policies": customers_with_policies,
            "avg_policies_per_customer": round(avg_policies, 2),
        },
        "policies": {
            "total": len(policies),
            "active": len(active_policies),
            "total_monthly_premium": total_monthly_premium,
            "avg_premium": avg_premium,
            "expiring_30days": expiry_30days,
        },
        "consultations": {
            "total": len(consultations),
            "this_period": len(consultations_this_period),
            "pending_followups": pending_followups,
            "channels": channels,
        },
        "regional_top5": regional_top5,
        "insurer_top5": insurer_top5,
    }


def get_monthly_trend(conn, year: int) -> dict:
    """월별 신규 고객 / 신규 계약 추이 (12개월)."""
    customers = _customers(conn)
    policies = _policies(conn)
    labels = [f"{year}-{m:02d}" for m in range(1, 13)]
    customer_data: list[int] = []
    policy_data: list[int] = []

    for m in range(1, 13):
        _period, start, end = _period_window("month", year, m)
        customer_data.append(sum(1 for c in customers if _in_window(c.get("created_at"), start, end)))
        policy_data.append(sum(1 for p in policies if _in_window(p.get("start_date"), start, end)))

    return {
        "labels": labels,
        "datasets": [
            {"label": "신규 고객", "data": customer_data},
            {"label": "신규 계약", "data": policy_data},
        ],
    }


def get_regional_distribution(conn) -> dict:
    """지역별 고객 분포 (파이 차트용)."""
    counts: dict[str, int] = {}
    for c in _customers(conn):
        region = _region(c.get("address"))
        if region:
            counts[region] = counts.get(region, 0) + 1
    items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10]
    return {"labels": [i[0] for i in items], "data": [i[1] for i in items]}


def get_insurer_distribution(conn) -> dict:
    """보험사별 계약 분포 (바 차트용)."""
    counts: dict[str, int] = {}
    for p in _policies(conn):
        if not _active(p) or not p.get("insurer"):
            continue
        name = str(p["insurer"])
        counts[name] = counts.get(name, 0) + 1
    items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10]
    return {"labels": [i[0] for i in items], "data": [i[1] for i in items]}


def get_premium_distribution(conn) -> dict:
    """월 보험료 구간 분포."""
    buckets = [
        ("10만원 미만", 0, 100000),
        ("10~30만원", 100000, 300000),
        ("30~50만원", 300000, 500000),
        ("50만원 이상", 500000, None),
    ]
    data = [0 for _ in buckets]
    for p in _policies(conn):
        if not _active(p):
            continue
        amount = _monthly_premium(p)
        for idx, (_label, low, high) in enumerate(buckets):
            if amount >= low and (high is None or amount < high):
                data[idx] += 1
                break
    return {"labels": [b[0] for b in buckets], "data": data}


# 캐시 관리 (간단한 인메모리 캐시)
_cache: dict[str, tuple[datetime, dict]] = {}
_CACHE_TTL = timedelta(minutes=5)


def _cache_identity(conn) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2]) if row and len(row) > 2 else str(id(conn))
    except Exception:
        return str(id(conn))


def clear_cache() -> None:
    """테스트/수동 새로고침용 캐시 초기화."""
    _cache.clear()


def get_cached_summary(conn, period: str, year: int, month: Optional[int]) -> dict:
    """캐시된 통계 요약 반환 (5분 TTL)."""
    cache_key = f"{_cache_identity(conn)}:summary:{period}:{year}:{month}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if datetime.now() - cached_time < _CACHE_TTL:
            return cached_data
    data = calculate_summary(conn, period, year, month)
    _cache[cache_key] = (datetime.now(), data)
    return data


def get_cached_charts(conn, chart_type: str, year: int) -> dict:
    """캐시된 차트 데이터 반환 (5분 TTL)."""
    cache_key = f"{_cache_identity(conn)}:chart:{chart_type}:{year}"
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if datetime.now() - cached_time < _CACHE_TTL:
            return cached_data

    if chart_type == "monthly_trend":
        data = get_monthly_trend(conn, year)
    elif chart_type == "regional_pie":
        data = get_regional_distribution(conn)
    elif chart_type == "insurer_bar":
        data = get_insurer_distribution(conn)
    elif chart_type == "premium_distribution":
        data = get_premium_distribution(conn)
    else:
        data = {"labels": [], "data": []}

    result = {"chart_type": chart_type, "data": data}
    _cache[cache_key] = (datetime.now(), result)
    return result
