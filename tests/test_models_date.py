import pytest

from database.models import _check_date


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
