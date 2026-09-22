"""
Google Calendar 연동 모듈
고객 방문 일정을 자동으로 Calendar에 등록
"""
from typing import Dict, Optional
from datetime import datetime, timedelta
from .auth import get_calendar_service


def create_visit_event(customer: Dict, visit_datetime: str) -> dict:
    """
    고객 방문 일정을 Google Calendar에 등록
    
    Args:
        customer: 고객 정보 dict
            - name: 이름
            - birth_year: 출생연도 (2자리)
            - region: 지역
            - address: 상세주소
            - phone: 연락처
            - birth_date: 생년월일
            - memo: 특이사항
        visit_datetime: ISO 8601 형식 datetime (예: "2026-09-25T14:00:00")
    
    Returns:
        {"event_id": str, "event_url": str}
    """
    service = get_calendar_service()
    
    # 제목 형식: "YYMM 성명 출생연도(2자리) 지역"
    now = datetime.fromisoformat(visit_datetime)
    yymm = now.strftime("%y%m")
    title = f"{yymm} {customer.get('name', '')} {customer.get('birth_year', '')} {customer.get('region', '')}"
    
    # 설명 형식: "생년월일/보험료/연락처/주소/특이사항" (슬래시 구분)
    description_parts = [
        customer.get("birth_date", ""),
        customer.get("premium", ""),  # 보험료 (있으면)
        customer.get("phone", ""),
        customer.get("address", ""),
        customer.get("memo", "")
    ]
    description = "/".join(part for part in description_parts if part)
    
    # 1시간 일정
    start_dt = datetime.fromisoformat(visit_datetime)
    end_dt = start_dt + timedelta(hours=1)
    
    # 이벤트 생성
    event = {
        "summary": title,
        "description": description,
        "location": customer.get("address", ""),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Asia/Seoul"
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Asia/Seoul"
        }
    }
    
    result = service.events().insert(
        calendarId='primary',
        body=event
    ).execute()
    
    return {
        "event_id": result.get("id"),
        "event_url": result.get("htmlLink")
    }


def list_upcoming_visits(days_ahead: int = 7) -> list:
    """
    앞으로 N일 이내 방문 일정 조회
    
    Args:
        days_ahead: 조회 기간 (일)
    
    Returns:
        이벤트 리스트
    """
    service = get_calendar_service()
    
    now = datetime.utcnow()
    time_min = now.isoformat() + 'Z'
    time_max = (now + timedelta(days=days_ahead)).isoformat() + 'Z'
    
    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    return events_result.get('items', [])
