"""
통계 API 테스트.

통계 계산 및 차트 데이터 생성 테스트.
"""
import pytest
from datetime import datetime

# local-engine 모듈 import
import sys
sys.path.insert(0, "local-engine")

from database import statistics
from database.db import connect, init_schema
from database import repo


@pytest.fixture
def test_db():
    """테스트용 인메모리 DB."""
    conn = connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture
def sample_data(test_db):
    """샘플 고객/계약/상담 데이터 생성."""
    # 고객 3명 생성
    c1 = repo.create_customer(test_db, {
        "name": "강소임",
        "phone": "010-9430-1522",
        "birth_date": "1960-03-20",
        "gender": "F",
        "address": "전남 목포시"
    })
    
    c2 = repo.create_customer(test_db, {
        "name": "김영희",
        "phone": "010-1234-5678",
        "address": "서울 강남구"
    })
    
    c3 = repo.create_customer(test_db, {
        "name": "박철수",
        "phone": "010-8888-9999",
        "address": "경기 수원시"
    })
    
    # 보험계약 생성
    repo.create_policy(test_db, c1["id"], {
        "insurer": "삼성생명",
        "product_name": "건강보험",
        "premium": 300000,
        "payment_cycle": "MONTHLY",
        "start_date": "2025-01-01",
        "end_date": "2045-01-01",
        "status": "ACTIVE"
    })
    
    repo.create_policy(test_db, c2["id"], {
        "insurer": "KB손해보험",
        "product_name": "종신보험",
        "premium": 150000,
        "payment_cycle": "MONTHLY",
        "start_date": "2024-06-15",
        "status": "ACTIVE"
    })
    
    # 상담 이력 생성
    repo.create_consultation(test_db, c1["id"], {
        "consulted_at": "2026-09-15",
        "channel": "방문",
        "title": "건강검진 상담",
        "content": "건강검진 결과 논의"
    })
    
    return {"customers": [c1, c2, c3]}


def test_calculate_summary(test_db, sample_data):
    """통계 요약 계산 테스트."""
    summary = statistics.calculate_summary(test_db, period="all-time")
    
    assert summary["customers"]["total"] == 3
    assert summary["policies"]["total"] == 2
    assert summary["policies"]["active"] == 2
    assert summary["policies"]["total_monthly_premium"] == 450000
    assert summary["consultations"]["total"] == 1
    
    # 지역별 TOP 5
    assert len(summary["regional_top5"]) > 0
    
    # 보험사별 TOP 5
    assert len(summary["insurer_top5"]) == 2
    assert summary["insurer_top5"][0]["insurer"] == "삼성생명"


def test_monthly_trend(test_db, sample_data):
    """월별 추이 차트 데이터 테스트."""
    trend = statistics.get_monthly_trend(test_db, 2026)
    
    assert "labels" in trend
    assert "datasets" in trend
    assert len(trend["labels"]) == 12
    assert len(trend["datasets"]) == 2
    assert trend["datasets"][0]["label"] == "신규 고객"
    assert trend["datasets"][1]["label"] == "신규 계약"


def test_regional_distribution(test_db, sample_data):
    """지역별 분포 테스트."""
    regional = statistics.get_regional_distribution(test_db)
    
    assert "labels" in regional
    assert "data" in regional
    assert len(regional["labels"]) > 0
    assert len(regional["data"]) == len(regional["labels"])


def test_insurer_distribution(test_db, sample_data):
    """보험사별 분포 테스트."""
    insurer = statistics.get_insurer_distribution(test_db)
    
    assert "labels" in insurer
    assert "data" in insurer
    assert "삼성생명" in insurer["labels"]
    assert "KB손해보험" in insurer["labels"]


def test_cached_summary(test_db, sample_data):
    """캐시 기능 테스트."""
    # 첫 번째 호출
    summary1 = statistics.get_cached_summary(test_db, "month", 2026, 9)
    
    # 두 번째 호출 (캐시됨)
    summary2 = statistics.get_cached_summary(test_db, "month", 2026, 9)
    
    assert summary1 == summary2


def test_cached_charts(test_db, sample_data):
    """차트 캐시 테스트."""
    # 첫 번째 호출
    chart1 = statistics.get_cached_charts(test_db, "monthly_trend", 2026)
    
    # 두 번째 호출 (캐시됨)
    chart2 = statistics.get_cached_charts(test_db, "monthly_trend", 2026)
    
    assert chart1 == chart2
    assert chart1["chart_type"] == "monthly_trend"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
