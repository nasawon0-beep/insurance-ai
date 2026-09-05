"""
보험기간(insured_period) / 납입기간(payment_period) 원문 문자열에서
만기일 / 납입종료일을 계산한다. DB 비의존 — 순수 함수만 둔다.

반환 날짜는 항상 'YYYY-MM-DD' 문자열 (사전식 비교가 실제 날짜 비교와 일치).
연/월 가산 시 결과 월의 말일을 넘으면 말일로 클램프한다 (예: 1/31 + 1개월 → 2/28|29).
2/29 생일의 N세 만기가 평년에 걸리면 2/28 로 내린다.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple

_AGE_RE = re.compile(r"(\d{2,3})\s*세")
_MONTHS_RE = re.compile(r"(\d{1,3})\s*개\s*월")
_YEARS_RE = re.compile(r"(\d{1,2})\s*년")

_PAY_AGE_RE = re.compile(r"(\d{2,3})\s*세\s*납")
# "20년납" / "30년 납" / "매월납/20년" / "월납·20년" 등 납입주기 접두를 허용.
_PAY_YEARS_RE = re.compile(r"(\d{1,3})\s*년\s*납")
_PAY_YEARS_SLASH_RE = re.compile(r"납\s*[/·]\s*(\d{1,3})\s*년")

_DIM = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _last_day(y: int, m: int) -> int:
    if m == 2 and _is_leap(y):
        return 29
    return _DIM[m - 1]


def _valid_date(s) -> bool:
    """YYYY-MM-DD. 0패딩 생략("1991-2-1")과 ISO datetime("2026-01-26T..") 앞 10자도 허용."""
    if s is None:
        return False
    try:
        datetime.strptime(str(s)[:10], "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _ymd(s: str) -> Tuple[int, int, int]:
    # strptime 으로 파싱 — 0패딩 안 된 값("1991-2-1")과 ISO datetime("2026-01-26T..") 도 받는다.
    dt = datetime.strptime(str(s)[:10], "%Y-%m-%d")
    return dt.year, dt.month, dt.day


def _clamp(y: int, m: int, d: int) -> str:
    d = min(d, _last_day(y, m))
    return f"{y:04d}-{m:02d}-{d:02d}"


def _add_months(y: int, m: int, d: int, months: int) -> str:
    total = y * 12 + (m - 1) + months
    ny, nm = divmod(total, 12)
    return _clamp(ny, nm + 1, d)


def parse_insured_period(s) -> Optional[Tuple[str, int]]:
    """보험기간 원문 → (kind, n). kind ∈ {'whole','age','years','months'}.

    우선순위: 'N세'(만기 문구 선택) > '종신' > 'N개월' > 'N년'. 해석 불가 시 None.
    ('세'와 '년'이 섞여 있으면 '세'가 이기고, '종신'만 있고 'N세'가 없으면 종신.)
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    m = _AGE_RE.search(t)
    if m:
        return ("age", int(m.group(1)))
    if "종신" in t:
        return ("whole", 0)
    m = _MONTHS_RE.search(t)
    if m:
        return ("months", int(m.group(1)))
    m = _YEARS_RE.search(t)
    if m:
        return ("years", int(m.group(1)))
    return None


def compute_end_date(
    insured_period,
    birth_date: Optional[str],
    start_date: Optional[str],
    issued_date: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """(만기일 or None, 경고 or None).

    - 종신 / 해석불가 → (None, None)
    - N세 만기 & 생년월일 없음 → (None, 경고)
    - N년 / N개월 & 시작일(계약일→발행일) 없음 → (None, 경고)
    """
    parsed = parse_insured_period(insured_period)
    if parsed is None:
        return (None, None)
    kind, n = parsed
    if kind == "whole":
        return (None, None)

    if kind == "age":
        if not birth_date or not _valid_date(birth_date):
            return (
                None,
                f"보험기간이 '{insured_period}'(N세 만기)이지만 생년월일이 없어 만기일을 계산할 수 없습니다.",
            )
        by, bm, bd = _ymd(birth_date)
        return (_clamp(by + n, bm, bd), None)

    base = start_date if (start_date and _valid_date(start_date)) else issued_date
    if not base or not _valid_date(base):
        return (
            None,
            f"보험기간이 '{insured_period}'이지만 계약 시작일(또는 발행일)이 없어 만기일을 계산할 수 없습니다.",
        )
    y, m, d = _ymd(base)
    if kind == "years":
        return (_clamp(y + n, m, d), None)
    if kind == "months":
        return (_add_months(y, m, d, n), None)
    return (None, None)


def compute_payment_end_date(
    payment_period,
    birth_date: Optional[str],
    start_date: Optional[str],
    computed_end_date: Optional[str],
    issued_date: Optional[str] = None,
) -> Optional[str]:
    """납입종료일. 'N년납'/'매월납/N년'→시작일(없으면 발행일)+N년, 'N세납'→생년+N,
    '전기납'→만기일, '일시납'→시작일, '종신납'/기타→None."""
    if not payment_period:
        return None
    t = str(payment_period).strip()
    if "전기납" in t:
        return computed_end_date or None
    base = start_date if (start_date and _valid_date(start_date)) else issued_date
    base = base if (base and _valid_date(base)) else None
    if "일시납" in t:
        return base
    if "종신납" in t:
        return None
    m = _PAY_AGE_RE.search(t)
    if m and birth_date and _valid_date(birth_date):
        by, bm, bd = _ymd(birth_date)
        return _clamp(by + int(m.group(1)), bm, bd)
    m = _PAY_YEARS_RE.search(t) or _PAY_YEARS_SLASH_RE.search(t)
    if m and base:
        y, mo, d = _ymd(base)
        return _clamp(y + int(m.group(1)), mo, d)
    return None
