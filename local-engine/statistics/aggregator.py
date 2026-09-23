"""EnhancedDashboard 통계 집계기.

실제 집계 구현은 기존 database 패키지의 암호화/DB 유틸과 함께 동작해야 하므로
`database.statistics`를 단일 구현으로 사용한다. 이 모듈은 Phase 2-C 설계서의
`local-engine/statistics/aggregator.py` 경로를 제공하는 얇은 호환 계층이다.
"""
from __future__ import annotations

from database.statistics import (  # noqa: F401
    calculate_summary,
    clear_cache,
    get_cached_charts,
    get_cached_summary,
    get_insurer_distribution,
    get_monthly_trend,
    get_premium_distribution,
    get_regional_distribution,
)
