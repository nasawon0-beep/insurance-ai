from collections import Counter

from database.coverage_analysis_parser import parse_coverage_analysis
from database.policy_coverages import DocumentType, detect_candidate_pages
from pdf_fixture_resolver import private_pdf


def test_private_real_pdf_detailed_analysis():
    pdf_path = private_pdf("coverage_case_02")

    candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
    print("\n=== 후보 페이지 ===")
    for key, pages in candidate_pages.items():
        print(f"{key}: {pages}")

    coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

    print(f"\n=== 총 파싱 항목 수: {len(coverages)}개 ===")

    category_count = Counter(cov.category for cov in coverages)
    print("\n=== 카테고리별 개수 ===")
    for category, count in sorted(category_count.items(), key=lambda item: -item[1]):
        print(f"{category}: {count}개")

    source_count = Counter(cov.source_type for cov in coverages)
    print("\n=== 소스 타입별 개수 ===")
    for source, count in source_count.items():
        print(f"{source}: {count}개")

    print("\n=== 카테고리별 항목 상세 ===")
    for category in ["사망", "암", "뇌혈관", "심장", "수술비", "입원비", "장해", "운전자", "기타"]:
        items = [coverage for coverage in coverages if coverage.category == category]
        if items:
            print(f"\n{category} ({len(items)}개):")
            for item in items:
                print(f"  - {item.coverage_name} (가입: {item.amount_text}, 페이지: {item.source_page})")

    assert len(coverages) == 50
