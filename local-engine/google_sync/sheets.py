"""
Google Sheets 동기화 모듈
보험DB를 Google Sheets와 양방향 동기화
"""
from typing import List, Dict, Optional
from datetime import datetime
from .auth import get_sheets_service

# 기존 보험DB 스프레드시트 ID (메모리에서 확인)
SPREADSHEET_ID = "13MJfUUCaQbHgb-HWyGcXhlmZweCxFgrI-XxqUnKjq0U"

# 11열 구조: DB구분|날짜|지역|통화시간|방문조건|이름|연락처|상세주소|생년월일|성별|전체메모
HEADER_ROW = [
    "DB구분", "날짜", "지역", "통화시간", "방문조건",
    "이름", "연락처", "상세주소", "생년월일", "성별", "전체메모"
]


def sync_to_sheets(customers: List[Dict], sheet_name: Optional[str] = None) -> dict:
    """
    고객 데이터를 Google Sheets에 추가
    
    Args:
        customers: 고객 데이터 리스트 (각 dict는 11개 필드 포함)
        sheet_name: 시트 이름 (기본: 현재 년월, 예: "2026-09")
    
    Returns:
        {"appended_rows": int, "spreadsheet_url": str, "sheet_name": str}
    """
    service = get_sheets_service()
    
    # 기본 시트 이름: YYYY-MM 형식
    if sheet_name is None:
        from datetime import datetime
        sheet_name = datetime.now().strftime("%Y-%m")
    
    # 데이터 행 변환 (11열 구조)
    rows = []
    for c in customers:
        row = [
            c.get("db_type", ""),  # DB구분
            c.get("date", ""),  # 날짜
            c.get("region", ""),  # 지역
            c.get("call_duration", ""),  # 통화시간
            c.get("visit_condition", ""),  # 방문조건
            c.get("name", ""),  # 이름
            c.get("phone", ""),  # 연락처
            c.get("address", ""),  # 상세주소
            c.get("birth_date", ""),  # 생년월일
            c.get("gender", ""),  # 성별
            c.get("memo", "")  # 전체메모
        ]
        rows.append(row)
    
    # 지정된 시트에 행 추가 (append)
    range_name = f"{sheet_name}!A:K"  # 11열 (A~K)
    body = {"values": rows}
    result = service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()
    
    return {
        "appended_rows": result.get("updates", {}).get("updatedRows", 0),
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}",
        "sheet_name": sheet_name
    }


def read_from_sheets(range_name: str = "Sheet1!A:K") -> List[List[str]]:
    """
    Google Sheets에서 데이터 읽기
    
    Args:
        range_name: 읽을 범위 (기본: Sheet1 전체 11열)
    
    Returns:
        행 데이터 리스트
    """
    service = get_sheets_service()
    
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name
    ).execute()
    
    return result.get("values", [])
