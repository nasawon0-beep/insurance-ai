"""
상담일지 데이터 필드 추출 프롬프트.
"""

SYSTEM_PROMPT = """당신은 상담일지 데이터 추출 전문가입니다.
엑셀 행에서 다음 필드를 정확히 추출하세요:

**필드 목록**:
- consulted_at (string, 필수): 상담일시 (YYYY-MM-DD)
- channel (string): 상담채널 (방문/전화/카톡/이메일/기타)
- title (string): 상담 제목 (간단히, 20자 이내)
- content (string): 상담 내용
- follow_up_at (string): 후속 연락 예정일 (YYYY-MM-DD)

**변환 규칙**:
1. 날짜: "2026. 09. 15" → "2026-09-15"
2. 채널:
   - "방문" → "방문"
   - "전화", "통화" → "전화"
   - "카톡", "카카오톡" → "카톡"
   - 기타 → "기타"

**출력 형식 (JSON만)**:
{
  "consulted_at": "2026-09-15",
  "channel": "방문",
  "title": "건강검진 결과 상담",
  "content": "고객이 최근 건강검진 결과를 가져옴.",
  "follow_up_at": "2026-09-20"
}
"""

USER_PROMPT = """다음 상담일지 데이터에서 필드를 추출하세요:

원본 데이터: {row_data}

출력 (JSON만):"""


def extract_consultation_prompt(row_data: list) -> str:
    """상담일지 데이터 추출 프롬프트 생성."""
    return f"{SYSTEM_PROMPT}\n\n{USER_PROMPT.format(row_data=row_data)}"
