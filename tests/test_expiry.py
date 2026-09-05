"""expiry.py 순수 함수 유닛테스트 (DB 불필요)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))

from database import expiry  # noqa: E402


# ---------- parse_insured_period ----------

def test_parse_whole_life():
    assert expiry.parse_insured_period("종신") == ("whole", 0)
    assert expiry.parse_insured_period("종신보장") == ("whole", 0)


def test_parse_age():
    assert expiry.parse_insured_period("100세") == ("age", 100)
    assert expiry.parse_insured_period("90세만기") == ("age", 90)
    assert expiry.parse_insured_period("80 세 만기") == ("age", 80)


def test_parse_years_and_months():
    assert expiry.parse_insured_period("20년") == ("years", 20)
    assert expiry.parse_insured_period("20년만기") == ("years", 20)
    assert expiry.parse_insured_period("120개월") == ("months", 120)
    assert expiry.parse_insured_period("6 개월") == ("months", 6)


def test_parse_age_wins_over_years_and_whole():
    # '세'와 '년' 혼재 → '세' 우선
    assert expiry.parse_insured_period("100세(20년납)") == ("age", 100)
    # '종신'과 'N세' 혼재 → 'N세' 우선
    assert expiry.parse_insured_period("종신 (100세만기)") == ("age", 100)


def test_parse_unparseable():
    assert expiry.parse_insured_period(None) is None
    assert expiry.parse_insured_period("") is None
    assert expiry.parse_insured_period("무배당") is None


# ---------- compute_end_date ----------

def test_end_date_age():
    d, w = expiry.compute_end_date("80세만기", "1970-05-10", "2020-01-01", "2020-01-01")
    assert d == "2050-05-10" and w is None


def test_end_date_age_feb29_leap_year_kept():
    d, w = expiry.compute_end_date("80세", "2000-02-29", None, None)
    assert d == "2080-02-29" and w is None  # 2080 윤년


def test_end_date_age_feb29_common_year_clamped():
    d, w = expiry.compute_end_date("83세", "2000-02-29", None, None)
    assert d == "2083-02-28" and w is None  # 2083 평년 → 2/28


def test_end_date_years_from_start():
    d, w = expiry.compute_end_date("10년", "1980-01-01", "2020-03-15", None)
    assert d == "2030-03-15" and w is None


def test_end_date_years_falls_back_to_issued():
    d, w = expiry.compute_end_date("5년", None, None, "2019-06-01")
    assert d == "2024-06-01" and w is None


def test_end_date_months_month_end_clamp():
    d, _ = expiry.compute_end_date("1개월", None, "2021-01-31", None)
    assert d == "2021-02-28"
    d2, _ = expiry.compute_end_date("1개월", None, "2020-01-31", None)
    assert d2 == "2020-02-29"  # 2020 윤년


def test_end_date_age_without_birthdate_warns():
    d, w = expiry.compute_end_date("100세", None, "2020-01-01", "2020-01-01")
    assert d is None and w and "생년월일" in w


def test_end_date_years_without_start_or_issued_warns():
    d, w = expiry.compute_end_date("20년", "1980-01-01", None, None)
    assert d is None and w is not None


def test_end_date_whole_life_and_unparseable_are_none():
    assert expiry.compute_end_date("종신", "1980-01-01", "2020-01-01", None) == (None, None)
    assert expiry.compute_end_date("무배당", "1980-01-01", "2020-01-01", None) == (None, None)


# ---------- compute_payment_end_date ----------

def test_payment_years():
    assert expiry.compute_payment_end_date("20년납", None, "2020-01-01", "2050-01-01") == "2040-01-01"


def test_payment_age():
    assert expiry.compute_payment_end_date("70세납", "1980-05-10", "2020-01-01", None) == "2050-05-10"


def test_payment_full_term_uses_end_date():
    assert expiry.compute_payment_end_date("전기납", None, "2020-01-01", "2055-01-01") == "2055-01-01"


def test_payment_lump_sum_uses_start():
    assert expiry.compute_payment_end_date("일시납", None, "2020-01-01", "2055-01-01") == "2020-01-01"


def test_payment_whole_life_and_other_are_none():
    assert expiry.compute_payment_end_date("종신납", "1980-01-01", "2020-01-01", "2055-01-01") is None
    assert expiry.compute_payment_end_date("", None, "2020-01-01", "2055-01-01") is None
    assert expiry.compute_payment_end_date("자유납", None, "2020-01-01", "2055-01-01") is None


# ---------- 검수 후 보강 (M2 0패딩·datetime / M3 매월납/N년) ----------

def test_end_date_accepts_unpadded_and_datetime_dates():
    # 0패딩 안 된 생년월일 → 500 나지 않고 정상 계산
    assert expiry.compute_end_date("90세만기", "1991-2-1", None, None) == ("2081-02-01", None)
    # start_date 가 ISO datetime 문자열이어도 앞 10자로 계산
    assert expiry.compute_end_date("20년만기", None, "2026-01-26T19:00:45", None) == ("2046-01-26", None)


def test_payment_end_date_matches_monthly_slash_format():
    # 추출 파이프라인이 실제로 저장하는 "매월납/20년" 형태
    assert expiry.compute_payment_end_date("매월납/20년", None, "2020-03-10", "2050-01-01") == "2040-03-10"
    assert expiry.compute_payment_end_date("매월납/10년", None, None, None, issued_date="2025-10-28") == "2035-10-28"
