"""
통계 API 테스트.

통계 계산 및 차트 데이터 생성 테스트.
"""
import pytest
from datetime import datetime
from threading import Event, Thread

# local-engine 모듈 import
import sys
sys.path.insert(0, "local-engine")

from database import statistics
from database.db import connect, init_schema
from database import dedupe_policies, policy_coverages, repo


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


def test_statistics_counts_only_confirmed_policies_through_selection_lifecycle(test_db):
    customer = repo.create_customer(test_db, {"name": "합성통계고객"})
    policy = repo.create_policy(test_db, customer["id"], {
        "product_name": "합성 참고계약",
        "premium": 20_000,
        "payment_cycle": "MONTHLY",
        "status": "ACTIVE",
        "document_status": "REFERENCE",
        "source_document_type": "product_description",
    })
    created = policy_coverages.create_many(test_db, customer["id"], [{
        "policy_id": policy["id"], "source_hash": "statistics-source",
        "source_page": 3, "source_table": 1, "source_row": 1,
        "rider_name": "합성 선택담보", "standard_name": "기타",
        "amount": 1_000_000, "amount_status": "known",
        "document_status": "REFERENCE",
    }], require_review=True)["items"][0]

    before = statistics.calculate_summary(test_db, period="all-time")
    assert before["policies"]["total"] == 0
    assert before["policies"]["active"] == 0
    assert before["policies"]["total_monthly_premium"] == 0

    repo.update_policy(test_db, policy["id"], {
        "document_status": "CONFIRMED",
        "confirmed_coverages": [{
            "id": created["id"], "source_page": 3, "source_table": 1, "source_row": 1,
        }],
    })
    confirmed = statistics.calculate_summary(test_db, period="all-time")
    assert confirmed["policies"]["total"] == 1
    assert confirmed["policies"]["active"] == 1
    assert confirmed["policies"]["total_monthly_premium"] == 20_000

    repo.update_policy(test_db, policy["id"], {"document_status": "REFERENCE"})
    cancelled = statistics.calculate_summary(test_db, period="all-time")
    assert cancelled["policies"]["total"] == 0
    assert cancelled["policies"]["active"] == 0
    assert cancelled["policies"]["total_monthly_premium"] == 0

    legacy = repo.create_policy(test_db, customer["id"], {
        "product_name": "기존 확정계약", "premium": 10_000, "status": "ACTIVE",
    })
    assert legacy["document_status"] == "CONFIRMED"
    legacy_summary = statistics.calculate_summary(test_db, period="all-time")
    assert legacy_summary["policies"]["total"] == 1
    assert legacy_summary["policies"]["total_monthly_premium"] == 10_000


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


def test_same_query_summary_and_chart_cache_follow_policy_selection_lifecycle(test_db):
    statistics.clear_cache()
    customer = repo.create_customer(test_db, {"name": "합성캐시고객"})
    policy = repo.create_policy(test_db, customer["id"], {
        "insurer": "합성보험", "product_name": "합성 참고계약",
        "premium": 20_000, "payment_cycle": "MONTHLY", "status": "ACTIVE",
        "document_status": "REFERENCE", "source_document_type": "product_description",
    })
    coverage = policy_coverages.create_many(test_db, customer["id"], [{
        "policy_id": policy["id"], "source_hash": "cache-lifecycle-source",
        "source_page": 1, "source_table": 1, "source_row": 1,
        "rider_name": "합성담보", "standard_name": "기타",
        "amount": 1_000_000, "document_status": "REFERENCE",
    }], require_review=True)["items"][0]

    summary_args = ("all-time", 2026, None)
    chart_args = ("insurer_bar", 2026)
    before = statistics.get_cached_summary(test_db, *summary_args)
    before_chart = statistics.get_cached_charts(test_db, *chart_args)
    assert statistics.get_cached_summary(test_db, *summary_args) is before
    assert statistics.get_cached_charts(test_db, *chart_args) is before_chart
    assert before["policies"]["total_monthly_premium"] == 0
    assert "합성보험" not in before_chart["data"]["labels"]

    repo.update_policy(test_db, policy["id"], {
        "document_status": "CONFIRMED",
        "confirmed_coverages": [{
            "id": coverage["id"], "source_page": 1, "source_table": 1, "source_row": 1,
        }],
    })
    selected = statistics.get_cached_summary(test_db, *summary_args)
    selected_chart = statistics.get_cached_charts(test_db, *chart_args)
    assert selected["policies"]["total_monthly_premium"] == 20_000
    assert selected["policies"]["active"] == 1
    assert "합성보험" in selected_chart["data"]["labels"]
    assert statistics.get_cached_summary(test_db, *summary_args) is selected
    assert statistics.get_cached_charts(test_db, *chart_args) is selected_chart

    repo.update_policy(test_db, policy["id"], {"document_status": "REFERENCE"})
    cancelled = statistics.get_cached_summary(test_db, *summary_args)
    cancelled_chart = statistics.get_cached_charts(test_db, *chart_args)
    assert cancelled["policies"]["total_monthly_premium"] == 0
    assert cancelled["policies"]["active"] == 0
    assert "합성보험" not in cancelled_chart["data"]["labels"]


def test_policy_create_update_delete_invalidate_same_cached_queries(test_db):
    statistics.clear_cache()
    customer = repo.create_customer(test_db, {"name": "합성CRUD고객"})
    args = ("all-time", 2026, None)
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total"] == 0

    policy = repo.create_policy(test_db, customer["id"], {
        "insurer": "변경전보험", "product_name": "합성계약",
        "premium": 10_000, "status": "ACTIVE",
    })
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total_monthly_premium"] == 10_000
    chart = statistics.get_cached_charts(test_db, "insurer_bar", 2026)
    assert "변경전보험" in chart["data"]["labels"]

    repo.update_policy(test_db, policy["id"], {"insurer": "변경후보험", "premium": 30_000})
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total_monthly_premium"] == 30_000
    chart = statistics.get_cached_charts(test_db, "insurer_bar", 2026)
    assert "변경전보험" not in chart["data"]["labels"]
    assert "변경후보험" in chart["data"]["labels"]

    assert repo.delete_policy(test_db, policy["id"])
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total"] == 0
    assert "변경후보험" not in statistics.get_cached_charts(test_db, "insurer_bar", 2026)["data"]["labels"]


def test_policy_dedupe_invalidates_cached_policy_count(test_db):
    statistics.clear_cache()
    customer = repo.create_customer(test_db, {"name": "합성중복고객"})
    policy_data = {
        "insurer": "합성보험", "product_name": "합성중복계약",
        "policy_number": "SYNTHETIC-1", "start_date": "2026-01-01",
        "premium": 10_000, "status": "ACTIVE",
    }
    repo.create_policy(test_db, customer["id"], policy_data)
    repo.create_policy(test_db, customer["id"], policy_data)
    args = ("all-time", 2026, None)
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total"] == 2

    plan = dedupe_policies.plan(test_db, customer["id"])
    assert plan["remove_count"] == 1
    result = dedupe_policies.apply_plan(test_db, plan)
    assert result["deleted_policies"] == 1
    assert statistics.get_cached_summary(test_db, *args)["policies"]["total"] == 1


def test_customer_and_consultation_crud_invalidate_affected_cached_queries(test_db):
    statistics.clear_cache()
    args = ("all-time", 2026, None)
    assert statistics.get_cached_summary(test_db, *args)["customers"]["total"] == 0

    customer = repo.create_customer(test_db, {"name": "합성집계고객", "address": "서울 합성구"})
    assert statistics.get_cached_summary(test_db, *args)["customers"]["total"] == 1
    regional = statistics.get_cached_charts(test_db, "regional_pie", 2026)
    assert "서울" in regional["data"]["labels"]

    repo.update_customer(test_db, customer["id"], {"address": "부산 합성구"})
    regional = statistics.get_cached_charts(test_db, "regional_pie", 2026)
    assert "서울" not in regional["data"]["labels"]
    assert "부산" in regional["data"]["labels"]

    consultation = repo.create_consultation(test_db, customer["id"], {
        "consulted_at": "2026-10-20", "channel": "방문", "title": "합성상담",
    })
    cached = statistics.get_cached_summary(test_db, *args)
    assert cached["consultations"]["total"] == 1
    repo.update_consultation(test_db, consultation["id"], {"channel": "전화"})
    assert statistics.get_cached_summary(test_db, *args) is not cached

    assert repo.delete_consultation(test_db, consultation["id"])
    assert statistics.get_cached_summary(test_db, *args)["consultations"]["total"] == 0
    assert repo.delete_customer(test_db, customer["id"])
    assert statistics.get_cached_summary(test_db, *args)["customers"]["total"] == 0


def test_failed_policy_selection_does_not_mutate_db_or_cached_result(test_db):
    statistics.clear_cache()
    customer = repo.create_customer(test_db, {"name": "합성실패고객"})
    policy = repo.create_policy(test_db, customer["id"], {
        "product_name": "합성 참고계약", "premium": 20_000,
        "status": "ACTIVE", "document_status": "REFERENCE",
        "source_document_type": "product_description",
    })
    cached = statistics.get_cached_summary(test_db, "all-time", 2026, None)
    with pytest.raises(ValueError):
        repo.update_policy(test_db, policy["id"], {"document_status": "CONFIRMED"})
    assert repo.get_policy(test_db, policy["id"])["document_status"] == "REFERENCE"
    assert statistics.get_cached_summary(test_db, "all-time", 2026, None) is cached


def test_invalidation_prevents_inflight_stale_cache_reinsertion(test_db, monkeypatch):
    statistics.clear_cache()
    calculation_started = Event()
    release_calculation = Event()

    def stale_calculation(*_args, **_kwargs):
        calculation_started.set()
        assert release_calculation.wait(timeout=2)
        return {"value": "stale"}

    monkeypatch.setattr(statistics, "calculate_summary", stale_calculation)
    result = {}
    worker = Thread(
        target=lambda: result.setdefault(
            "first", statistics.get_cached_summary(test_db, "all-time", 2026, None)
        )
    )
    worker.start()
    assert calculation_started.wait(timeout=2)
    statistics.invalidate_cache(test_db)
    release_calculation.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result["first"] == {"value": "stale"}

    monkeypatch.setattr(statistics, "calculate_summary", lambda *_args, **_kwargs: {"value": "fresh"})
    assert statistics.get_cached_summary(test_db, "all-time", 2026, None) == {"value": "fresh"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
