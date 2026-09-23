"""
AI 분류기 테스트.

컬럼 매핑 및 필드 추출 테스트 (규칙 기반).
"""
import pytest

# local-engine 모듈 import
import sys
sys.path.insert(0, "local-engine")

from database.import_data import ai_classifier


def test_classify_column_rule():
    """규칙 기반 컬럼 분류 테스트."""
    assert ai_classifier.classify_column_rule("고객명") == "name"
    assert ai_classifier.classify_column_rule("이름") == "name"
    assert ai_classifier.classify_column_rule("Customer Name") == "name"
    
    assert ai_classifier.classify_column_rule("전화번호") == "phone"
    assert ai_classifier.classify_column_rule("연락처") == "phone"
    assert ai_classifier.classify_column_rule("Phone") == "phone"
    
    assert ai_classifier.classify_column_rule("생년월일") == "birth_date"
    assert ai_classifier.classify_column_rule("생일") == "birth_date"
    
    assert ai_classifier.classify_column_rule("성별") == "gender"
    assert ai_classifier.classify_column_rule("이메일") == "email"
    assert ai_classifier.classify_column_rule("주소") == "address"
    assert ai_classifier.classify_column_rule("메모") == "memo"
    
    assert ai_classifier.classify_column_rule("알 수 없음") is None


def test_auto_map_columns_sync():
    """컬럼 자동 매핑 테스트 (동기 버전)."""
    columns = [
        "고객명",
        "전화번호",
        "생년월일",
        "성별",
        "주소",
        "메모"
    ]
    
    mapping = ai_classifier.auto_map_columns_sync(columns)
    
    assert mapping.get("name") == "고객명"
    assert mapping.get("phone") == "전화번호"
    assert mapping.get("birth_date") == "생년월일"
    assert mapping.get("gender") == "성별"
    assert mapping.get("address") == "주소"
    assert mapping.get("memo") == "메모"


def test_auto_map_english_columns():
    """영문 컬럼 매핑 테스트."""
    columns = [
        "Customer Name",
        "Phone",
        "Email",
        "Address"
    ]
    
    mapping = ai_classifier.auto_map_columns_sync(columns)
    
    assert mapping.get("name") == "Customer Name"
    assert mapping.get("phone") == "Phone"
    assert mapping.get("email") == "Email"
    assert mapping.get("address") == "Address"


def test_auto_map_mixed_columns():
    """혼합 컬럼 매핑 테스트."""
    columns = [
        "이름",
        "Phone Number",
        "생년월일",
        "Email",
        "주소지"
    ]
    
    mapping = ai_classifier.auto_map_columns_sync(columns)
    
    assert mapping.get("name") == "이름"
    assert mapping.get("phone") == "Phone Number"
    assert mapping.get("birth_date") == "생년월일"
    assert mapping.get("email") == "Email"
    # "주소지"는 "주소" 키워드 포함
    assert mapping.get("address") == "주소지"


def test_auto_map_insurance_schedule_columns():
    """insurance-schedule-agent 형식 컬럼 매핑."""
    columns = [
        "DB구분",
        "날짜",
        "지역",
        "통화 시간",
        "방문 조건",
        "이름",
        "연락처",
        "상세 주소",
        "생년월일",
        "성별",
        "전체 메모"
    ]
    
    mapping = ai_classifier.auto_map_columns_sync(columns)
    
    assert mapping.get("name") == "이름"
    assert mapping.get("phone") == "연락처"
    assert mapping.get("birth_date") == "생년월일"
    assert mapping.get("gender") == "성별"
    # "상세 주소"는 "주소" 키워드 포함
    assert mapping.get("address") == "상세 주소"
    # "전체 메모"는 "메모" 키워드 포함
    assert mapping.get("memo") == "전체 메모"


def test_empty_columns():
    """빈 컬럼 처리 테스트."""
    columns = ["", "  ", "이름", None]
    
    mapping = ai_classifier.auto_map_columns_sync(columns)
    
    # 빈 컬럼은 무시
    assert len(mapping) == 1
    assert mapping.get("name") == "이름"


def test_fallback_classify_rows_by_domain():
    """행 데이터는 고객/보험계약/상담일지로 규칙 폴백 분류된다."""
    customer = ["강소임", "010-9430-1522", "19600320", "여", "전남 목포시"]
    policy = ["삼성생명", "건강보험", "30만원", "월납", "2025-01-01"]
    consultation = ["2026. 09. 15", "방문", "건강검진 결과 상담", "2026. 09. 20"]

    assert ai_classifier.fallback_classify(customer)["category"] == "고객"
    assert ai_classifier.fallback_classify(policy)["category"] == "보험계약"
    assert ai_classifier.fallback_classify(consultation)["category"] == "상담일지"


def test_rule_extractors_normalize_phase2c_fields():
    """Phase 2-C 필드 추출 폴백은 날짜/전화/금액/성별을 정규화한다."""
    schedule_row = [
        "경실버", "2026. 09. 15", "전남 목포시", "오후", "평일",
        "강소임", "01094301522", "상동로33 현대아파트", "19600320", "여", "20만원이상"
    ]
    customer = ai_classifier.extract_customer_rule(schedule_row)
    assert customer["name"] == "강소임"
    assert customer["phone"] == "010-9430-1522"
    assert customer["birth_date"] == "1960-03-20"
    assert customer["gender"] == "F"
    assert customer["address"] == "전남 목포시 상동로33 현대아파트"

    policy = ai_classifier.extract_policy_rule(["삼성생명", "건강보험", "30만원", "월납", "2025. 01. 01", "2045/01/01", "유지"])
    assert policy["insurer"] == "삼성생명"
    assert policy["premium"] == 300000
    assert policy["payment_cycle"] == "MONTHLY"
    assert policy["start_date"] == "2025-01-01"
    assert policy["status"] == "ACTIVE"

    consultation = ai_classifier.extract_consultation_rule(["2026. 09. 15", "방문", "건강검진 결과 상담", "2026. 09. 20"])
    assert consultation["consulted_at"] == "2026-09-15"
    assert consultation["channel"] == "방문"
    assert consultation["follow_up_at"] == "2026-09-20"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
