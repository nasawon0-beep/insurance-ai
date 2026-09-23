"""
CSV 컬럼 헤더 및 엑셀 행 자동 분류 프롬프트.

Ollama qwen2.5:3b가 엑셀/CSV 데이터를 고객/보험계약/상담일지로
분류하고, 기존 CSV import 화면에서는 컬럼명을 고객 필드로 매핑한다.
"""

import json

CLASSIFY_SYSTEM_PROMPT = """당신은 보험 영업 데이터 전문가입니다.
엑셀 행을 분석하여 '고객', '보험계약', '상담일지' 중 하나로 정확히 분류하세요.

판단 기준:
1. 고객: 이름+전화번호가 있거나 생년월일, 성별, 주소 중 2개 이상 포함합니다.
2. 보험계약: 보험사명, 상품명, 증권번호, 보험료, 납입주기, 계약일/만기일이 포함됩니다.
3. 상담일지: 상담일자, 상담채널(방문/전화/카톡), 상담내용, 후속연락일이 포함됩니다.

출력 형식(JSON만):
{"category":"고객|보험계약|상담일지","confidence":0.0,"reasoning":"한 줄 판단 근거"}

주의사항:
- JSON 외 다른 텍스트를 출력하지 마세요.
- 애매하면 가장 가능성 높은 카테고리를 고르되 confidence를 낮게 주세요.
"""

CLASSIFY_EXAMPLES = [
    {
        "row_data": ["강소임", "010-9430-1522", "1960-03-20", "여", "전남 목포시"],
        "output": {"category": "고객", "confidence": 0.95, "reasoning": "이름, 전화번호, 생년월일, 성별, 주소 포함"},
    },
    {
        "row_data": ["삼성생명", "건강보험", "30만원", "월납", "2025-01-01"],
        "output": {"category": "보험계약", "confidence": 0.96, "reasoning": "보험사명, 상품명, 보험료, 납입주기 포함"},
    },
    {
        "row_data": ["2026. 09. 15", "방문", "건강검진 결과 상담", "2026. 09. 20"],
        "output": {"category": "상담일지", "confidence": 0.92, "reasoning": "상담일, 채널, 상담 내용, 후속일 포함"},
    },
]


def classify_row_prompt(row_data: list, row_index: int = 0) -> str:
    """엑셀 행의 3-way 분류 프롬프트 생성."""
    examples = "\n\n".join(
        f"예시 {idx + 1}:\n입력: {ex['row_data']}\n출력: {json.dumps(ex['output'], ensure_ascii=False)}"
        for idx, ex in enumerate(CLASSIFY_EXAMPLES)
    )
    return (
        f"{CLASSIFY_SYSTEM_PROMPT}\n\n{examples}\n\n---\n"
        f"행 번호: {row_index}\n데이터: {json.dumps(row_data, ensure_ascii=False)}\n\n출력(JSON만):"
    )

SYSTEM_PROMPT = """당신은 CSV 헤더 분류 전문가입니다.
주어진 컬럼 이름을 다음 필드 중 하나로 매핑하세요:

**고객 필드**:
- name: 이름, 고객명, 고객이름, customer_name
- phone: 전화번호, 연락처, 휴대폰, mobile, tel
- birth_date: 생년월일, 생일, 생일자, birthdate
- gender: 성별
- email: 이메일, 메일
- address: 주소, 지역
- occupation: 직업
- tags: 태그, 분류
- memo: 메모, 비고, 특이사항
- customer_status: 상태, 고객상태

**판단 기준**:
- 한글, 영문, 혼용 모두 지원
- 부분 매칭 허용 (예: "고객이름님" → name)
- 유사어 인식 (예: "연락처" → phone)
- 확신이 없으면 null 반환

**출력 형식 (JSON만)**:
{"field": "name|phone|birth_date|...|null", "confidence": 0.0~1.0}

**예시**:
입력: "고객명"
출력: {"field": "name", "confidence": 0.95}

입력: "연락처"
출력: {"field": "phone", "confidence": 0.9}

입력: "Customer Name"
출력: {"field": "name", "confidence": 0.95}

입력: "알 수 없음"
출력: {"field": null, "confidence": 0.0}
"""

USER_PROMPT = """다음 컬럼 이름을 고객 필드로 매핑하세요:

컬럼 이름: "{column_name}"

출력 (JSON만):"""


def classify_column_prompt(column_name: str) -> str:
    """컬럼 분류 프롬프트 생성."""
    return f"{SYSTEM_PROMPT}\n\n{USER_PROMPT.format(column_name=column_name)}"
