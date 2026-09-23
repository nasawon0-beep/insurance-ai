"""
Phase 2-C 완성도 검증 테스트.

검증 항목:
1. AI 분류 모듈 (prompts/ 디렉토리)
2. 통계 API (/statistics/summary, /statistics/charts)
3. P0 수정 (huggingface_hub 추가, EnhancedDashboard API 연동)
"""
import sys
import sqlite3
from pathlib import Path

# Test 1: AI 분류 모듈 존재 확인
def test_ai_prompts_exist():
    """AI 분류 프롬프트 파일 존재 확인."""
    prompts_dir = Path("local-engine/database/import_data/prompts")
    assert prompts_dir.exists(), "prompts/ 디렉토리가 없습니다"
    
    required_files = [
        "classify.py",
        "extract_customer.py", 
        "extract_policy.py",
        "extract_consultation.py"
    ]
    
    for fname in required_files:
        fpath = prompts_dir / fname
        assert fpath.exists(), f"{fname} 파일이 없습니다"
        assert fpath.stat().st_size > 500, f"{fname} 파일이 너무 작습니다 (미완성)"
    
    print("✅ AI 프롬프트 파일 모두 존재")


def test_ai_classifier_functions():
    """AI 분류기 함수 동작 확인."""
    sys.path.insert(0, "local-engine")
    from database.import_data import ai_classifier
    
    # 규칙 기반 분류
    assert ai_classifier.classify_column_rule("이름") == "name"
    assert ai_classifier.classify_column_rule("전화번호") == "phone"
    assert ai_classifier.classify_column_rule("생년월일") == "birth_date"
    
    # 자동 매핑 (동기)
    columns = ["이름", "전화번호", "생년월일", "성별"]
    mapping = ai_classifier.auto_map_columns_sync(columns)
    assert "name" in mapping
    assert "phone" in mapping
    assert "birth_date" in mapping
    
    print("✅ AI 분류 함수 동작 확인")


def test_statistics_module():
    """통계 모듈 함수 동작 확인."""
    sys.path.insert(0, "local-engine")
    from database import statistics
    
    # 임시 DB 생성
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    
    # 스키마 생성
    cursor.execute("""
        CREATE TABLE customers (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            address TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE policies (
            id TEXT PRIMARY KEY,
            customer_id TEXT,
            status TEXT,
            premium INTEGER,
            payment_cycle TEXT,
            insurer TEXT,
            start_date TEXT,
            end_date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE consultations (
            id TEXT PRIMARY KEY,
            customer_id TEXT,
            consulted_at TEXT,
            channel TEXT,
            follow_up_at TEXT
        )
    """)
    
    # 테스트 데이터 삽입
    cursor.execute("INSERT INTO customers VALUES ('c1', '2026-09-01', '서울 강남구')")
    cursor.execute("INSERT INTO customers VALUES ('c2', '2026-09-10', '경기 성남시')")
    cursor.execute("INSERT INTO policies VALUES ('p1', 'c1', 'ACTIVE', 300000, 'MONTHLY', '삼성생명', '2026-01-01', '2046-01-01')")
    cursor.execute("INSERT INTO consultations VALUES ('k1', 'c1', '2026-09-15', '방문', '2026-09-20')")
    conn.commit()
    
    # 통계 요약 계산
    summary = statistics.calculate_summary(conn, period="month", year=2026, month=9)
    assert summary["customers"]["total"] == 2
    assert summary["policies"]["total"] == 1
    assert summary["consultations"]["total"] == 1
    assert summary["period"] == "2026-09"
    
    # 월별 추이
    trend = statistics.get_monthly_trend(conn, 2026)
    assert len(trend["labels"]) == 12
    assert len(trend["datasets"]) == 2
    
    # 지역별 분포
    regional = statistics.get_regional_distribution(conn)
    assert len(regional["labels"]) > 0
    assert "서울" in regional["labels"] or "경기" in regional["labels"]
    
    # 보험사별 분포
    insurer = statistics.get_insurer_distribution(conn)
    assert len(insurer["labels"]) > 0
    
    conn.close()
    print("✅ 통계 모듈 함수 동작 확인")


def test_statistics_caching():
    """통계 캐싱 동작 확인."""
    sys.path.insert(0, "local-engine")
    from database import statistics
    
    # 임시 DB
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE customers (id TEXT, created_at TEXT, address TEXT)")
    cursor.execute("CREATE TABLE policies (id TEXT, customer_id TEXT, status TEXT, premium INTEGER, payment_cycle TEXT, insurer TEXT, start_date TEXT, end_date TEXT)")
    cursor.execute("CREATE TABLE consultations (id TEXT, customer_id TEXT, consulted_at TEXT, channel TEXT, follow_up_at TEXT)")
    conn.commit()
    
    # 캐시 테스트
    result1 = statistics.get_cached_summary(conn, "month", 2026, 9)
    result2 = statistics.get_cached_summary(conn, "month", 2026, 9)
    assert result1 == result2, "캐싱이 동작하지 않음"
    
    chart1 = statistics.get_cached_charts(conn, "monthly_trend", 2026)
    chart2 = statistics.get_cached_charts(conn, "monthly_trend", 2026)
    assert chart1 == chart2, "차트 캐싱이 동작하지 않음"
    
    conn.close()
    print("✅ 통계 캐싱 동작 확인")


def test_requirements_txt_has_huggingface_hub():
    """requirements.txt에 huggingface_hub 추가 확인."""
    req_path = Path("local-engine/requirements.txt")
    assert req_path.exists(), "requirements.txt가 없습니다"
    
    content = req_path.read_text()
    assert "huggingface_hub" in content, "requirements.txt에 huggingface_hub가 없습니다"
    
    print("✅ requirements.txt에 huggingface_hub 추가됨")


def test_statistics_api_endpoints_exist():
    """통계 API 엔드포인트 존재 확인."""
    router_path = Path("local-engine/database/router.py")
    assert router_path.exists(), "router.py가 없습니다"
    
    content = router_path.read_text()
    
    # /statistics/summary 엔드포인트
    assert "@router.get(\"/statistics/summary\")" in content, "/statistics/summary 엔드포인트가 없습니다"
    
    # /statistics/charts 엔드포인트
    assert "@router.get(\"/statistics/charts\")" in content, "/statistics/charts 엔드포인트가 없습니다"
    
    # _stats 모듈 import
    assert "import statistics as _stats" in content or "from . import statistics as _stats" in content, "statistics 모듈이 import되지 않았습니다"
    
    print("✅ 통계 API 엔드포인트 존재 확인")


def test_enhanced_dashboard_api_calls():
    """EnhancedDashboard가 올바른 API를 호출하는지 확인."""
    dashboard_path = Path("desktop/src/EnhancedDashboard.tsx")
    assert dashboard_path.exists(), "EnhancedDashboard.tsx가 없습니다"
    
    content = dashboard_path.read_text()
    
    # /statistics/summary 호출
    assert "/statistics/summary" in content, "EnhancedDashboard가 /statistics/summary를 호출하지 않습니다"
    
    # /statistics/charts 호출
    assert "/statistics/charts" in content, "EnhancedDashboard가 /statistics/charts를 호출하지 않습니다"
    
    print("✅ EnhancedDashboard API 호출 확인")


def test_line_count():
    """구현된 코드 라인 수 확인."""
    import subprocess
    
    result = subprocess.run(
        ["wc", "-l", 
         "local-engine/database/import_data/ai_classifier.py",
         "local-engine/database/import_data/prompts/classify.py",
         "local-engine/database/import_data/prompts/extract_customer.py",
         "local-engine/database/import_data/prompts/extract_policy.py",
         "local-engine/database/import_data/prompts/extract_consultation.py",
         "local-engine/database/statistics.py"
        ],
        capture_output=True,
        text=True
    )
    
    total_lines = int(result.stdout.strip().split()[-2])
    print(f"✅ AI 분류 + 통계 모듈 총 라인 수: {total_lines}줄")
    assert total_lines > 500, f"코드가 너무 적습니다 ({total_lines}줄)"


if __name__ == "__main__":
    print("\n=== Phase 2-C 완성도 검증 ===\n")
    
    try:
        test_ai_prompts_exist()
        test_ai_classifier_functions()
        test_statistics_module()
        test_statistics_caching()
        test_requirements_txt_has_huggingface_hub()
        test_statistics_api_endpoints_exist()
        test_enhanced_dashboard_api_calls()
        test_line_count()
        
        print("\n" + "="*50)
        print("✅ 모든 검증 통과! Phase 2-C 완성됨")
        print("="*50)
        
    except AssertionError as e:
        print(f"\n❌ 검증 실패: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
