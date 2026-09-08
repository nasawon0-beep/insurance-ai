import pytest

from database.models import _check_date, _check_datetime


@pytest.mark.parametrize("raw", [
    "20060816", "2006-0816", "2006.8.16", "2006/8/16", "2006 8 16", "2006-8-16",
])
def test_check_date_normalizes_loose_inputs(raw):
    assert _check_date(raw) == "2006-08-16"


def test_check_date_two_digit_year_rules():
    assert _check_date("600606", allow_2digit_birth=True) == "1960-06-06"
    assert _check_date("200606", allow_2digit_birth=True) == "2020-06-06"
    assert _check_date("600606") == "2060-06-06"


@pytest.mark.parametrize("raw", ["", None])
def test_check_date_preserves_empty(raw):
    assert _check_date(raw) == raw


@pytest.mark.parametrize("raw", ["2006-13-40", "abc", "2006-02-30"])
def test_check_date_rejects_invalid_dates(raw):
    with pytest.raises(ValueError, match="날짜는 YYYY-MM-DD 형식의 실제 달력 날짜여야 합니다"):
        _check_date(raw)


@pytest.mark.parametrize("raw", ["", None])
def test_check_datetime_preserves_empty(raw):
    assert _check_datetime(raw) == raw


def test_check_datetime_accepts_date_only_and_normalizes():
    assert _check_datetime("2026-9-8") == "2026-09-08"


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-08T08:30:00+00:00", "2026-09-08T08:30:00+00:00"),
    ("2026-09-08 08:30:00", "2026-09-08T08:30:00"),         # 공백 → 'T' 구분자 통일 (naive 유지)
    ("2026-09-08T08:30:00", "2026-09-08T08:30:00"),         # naive 는 naive 로 (없는 zone 을 UTC 라 단정 안 함)
    ("2026-09-08T17:30:00+09:00", "2026-09-08T08:30:00+00:00"),  # tz 있으면 UTC 로 환산
])
def test_check_datetime_normalizes_timestamp_format(raw, expected):
    assert _check_datetime(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "2026-13-01", "2026-09-08T99:99:99"])
def test_check_datetime_rejects_garbage(raw):
    with pytest.raises(ValueError):
        _check_datetime(raw)
