"""
주민등록번호 정규화 / 형식검증 / 마스킹.

주의:
- 2020-10 이후 발급분은 뒷 6자리가 임의화되어 기존 '검증번호(마지막 자리)' 규칙이
  폐지됐다. 그래서 체크섬 검증은 하지 않는다 (유효한 신규 번호를 오탐 거부하게 됨).
- 여기서 하는 건 형식 검증뿐: 13자리 숫자 + 생년월일 그럴듯함 + 성별자리 1~8.
- 화면에는 항상 mask() 결과만 노출한다. 전체값은 명시적 조회 + 접근 로그가 있을 때만.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

_DIGITS = re.compile(r"\d")


def normalize(raw: Optional[str]) -> Optional[str]:
    """하이픈/공백 제거 후 13자리 숫자 문자열. 형식이 아니면 ValueError."""
    if raw is None or str(raw).strip() == "":
        return None
    digits = "".join(_DIGITS.findall(str(raw)))
    if len(digits) != 13:
        raise ValueError("주민등록번호는 숫자 13자리여야 합니다.")

    mm = int(digits[2:4])
    dd = int(digits[4:6])
    if digits[6] not in "12345678":
        raise ValueError("주민등록번호의 성별 자리가 올바르지 않습니다.")
    century = 1900 if digits[6] in "1256" else 2000
    yy = int(digits[0:2])
    try:
        date(century + yy, mm, dd)
    except ValueError:
        raise ValueError("주민등록번호의 생년월일 부분이 올바르지 않습니다.")
    return digits


def mask(rrn13: Optional[str]) -> Optional[str]:
    """901010-1****** 형태. 입력이 정규화된 13자리라고 가정."""
    if not rrn13:
        return None
    return f"{rrn13[:6]}-{rrn13[6]}{'*' * 6}"


def birth_and_gender(raw: Optional[str]):
    """주민번호(정규화 전/후 무관) → (생년월일 'YYYY-MM-DD', 성별 'M'/'F').
    앞 6자리 + 성별자리가 생년월일·성별의 확정 근거. 형식이 아니면 (None, None)."""
    digits = "".join(_DIGITS.findall(str(raw or "")))
    if len(digits) != 13 or digits[6] not in "12345678":
        return None, None
    mm, dd = int(digits[2:4]), int(digits[4:6])
    century = 1900 if digits[6] in "1256" else 2000  # 1·2·5·6→1900년대 / 3·4·7·8→2000년대
    yy = int(digits[0:2])
    try:
        date(century + yy, mm, dd)
    except ValueError:
        return None, None
    birth = f"{century + yy:04d}-{digits[2:4]}-{digits[4:6]}"
    gender = "M" if digits[6] in "1357" else "F"
    return birth, gender
