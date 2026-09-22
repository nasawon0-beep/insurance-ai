"""
Google Workspace 연동 API 라우터
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from .sheets import sync_to_sheets, read_from_sheets
from .calendar import create_visit_event, list_upcoming_visits

router = APIRouter(prefix="/google", tags=["google"])


class CustomerSyncRequest(BaseModel):
    """Sheets 동기화 요청"""
    customers: List[dict]
    sheet_name: Optional[str] = None  # 기본: YYYY-MM


class CalendarEventRequest(BaseModel):
    """Calendar 일정 생성 요청"""
    customer: dict
    visit_datetime: str  # ISO 8601 형식


@router.post("/sheets/sync")
async def sync_customers_to_sheets(req: CustomerSyncRequest):
    """
    고객 데이터를 Google Sheets에 추가
    
    POST /google/sheets/sync
    Body: {
        "customers": [{db_type, date, region, ...}, ...],
        "sheet_name": "2026-09"  # Optional, 기본: 현재 년월
    }
    """
    try:
        result = sync_to_sheets(req.customers, req.sheet_name)
        return {
            "success": True,
            "appended_rows": result["appended_rows"],
            "spreadsheet_url": result["spreadsheet_url"],
            "sheet_name": result["sheet_name"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sheets/read")
async def read_sheets_data(range_name: str = "Sheet1!A:K"):
    """
    Google Sheets에서 데이터 읽기
    
    GET /google/sheets/read?range_name=Sheet1!A:K
    """
    try:
        rows = read_from_sheets(range_name)
        return {
            "success": True,
            "row_count": len(rows),
            "data": rows
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calendar/event")
async def create_calendar_event(req: CalendarEventRequest):
    """
    고객 방문 일정을 Google Calendar에 등록
    
    POST /google/calendar/event
    Body: {
        "customer": {name, birth_year, region, address, phone, birth_date, memo},
        "visit_datetime": "2026-09-25T14:00:00"
    }
    """
    try:
        result = create_visit_event(req.customer, req.visit_datetime)
        return {
            "success": True,
            "event_id": result["event_id"],
            "event_url": result["event_url"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar/upcoming")
async def get_upcoming_visits(days: int = 7):
    """
    앞으로 N일 이내 방문 일정 조회
    
    GET /google/calendar/upcoming?days=7
    """
    try:
        events = list_upcoming_visits(days)
        return {
            "success": True,
            "event_count": len(events),
            "events": events
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
