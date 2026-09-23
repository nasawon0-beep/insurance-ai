"""Phase 2-C 통계 API 라우터.

GET /statistics/summary
GET /statistics/charts
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database.router import get_conn
from . import aggregator

router = APIRouter(tags=["statistics"])


@router.get("/statistics/summary")
def get_statistics_summary(
    period: str = Query("month", pattern="^(month|quarter|year|all-time)$"),
    year: int = Query(2026, ge=1900, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    conn=Depends(get_conn),
):
    """고객/계약/상담 요약 통계."""
    try:
        return aggregator.get_cached_summary(conn, period, year, month)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"통계 조회 실패: {exc}")


@router.get("/statistics/charts")
def get_statistics_charts(
    chart_type: str = Query(
        "monthly_trend",
        pattern="^(monthly_trend|regional_pie|insurer_bar|premium_distribution)$",
    ),
    year: int = Query(2026, ge=1900, le=2100),
    conn=Depends(get_conn),
):
    """EnhancedDashboard 차트 데이터."""
    try:
        return aggregator.get_cached_charts(conn, chart_type, year)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"차트 조회 실패: {exc}")
