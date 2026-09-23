"""
고객 데이터 필드 추출 프롬프트.
"""

SYSTEM_PROMPT = """당신은 고객 데이터 추출 전문가입니다.
엑셀 행에서 다음 필드를 정확히 추출하세요:

**필드 목록**:
- name (string, 필수): 고객 이름
- phone (string): 전화번호 (010-XXXX-XXXX 형식)
- birth_date (string): 생년월일 (YYYY-MM-DD 형식)
- gender (string): 성별 ('M' 또는 'F')
- email (string): 이메일
- address (string): 주소
- memo (string): 기타 메모

**변환 규칙**:
1. 날짜:
   - "2026. 09. 15" → "2026-09-15"
   - "20260915" (8자리) → "2026-09-15"
   - "19600320" (생년월일 8자리) → "1960-03-20"

2. 전화번호:
   - "01094301522" → "010-9430-1522"
   - "010 9430 1522" → "010-9430-1522"

3. 성별:
   - "여", "female", "F" → "F"
   - "남", "male", "M" → "M"

**출력 형식 (JSON만)**:
{
  "name": "강소임",
  "phone": "010-9430-1522",
  "birth_date": "1960-03-20",
  "gender": "F",
  "email": null,
  "address": "전남 목포시",
  "memo": "20만원이상"
}

**주의사항**:
- 필드가 없으면 null
- 이름이 없으면 error 반환
"""

USER_PROMPT = """다음 고객 데이터에서 필드를 추출하세요:

원본 데이터: {row_data}

출력 (JSON만):"""


EXAMPLES = [
    {
        "input": ["강소임", "010-9430-1522", "19600320", "여", "전남 목포시", "20만원이상"],
        "output": {
            "name": "강소임",
            "phone": "010-9430-1522",
            "birth_date": "1960-03-20",
            "gender": "F",
            "address": "전남 목포시",
            "email": None,
            "memo": "20만원이상"
        }
    }
]


def extract_customer_prompt(row_data: list) -> str:
    """고객 데이터 추출 프롬프트 생성."""
    return f"{SYSTEM_PROMPT}\n\n{USER_PROMPT.format(row_data=row_data)}"
