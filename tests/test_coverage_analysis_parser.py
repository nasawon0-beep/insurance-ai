import pytest

from database.coverage_analysis_parser import parse_coverage_analysis
from database import coverage_analysis_parser
from database.policy_coverages import DocumentType, detect_candidate_pages
from private_pdf_fixtures import private_pdf


def test_headerless_legacy_summary_is_detected_and_parsed(monkeypatch):
    table = [
        ["사망\n장해", "상해사망", "2억", "1억 1,700만", "-8,300만", "부족"],
        [None, "질병사망", "1억", "0", "-1억", "미가입"],
    ]

    class FakePage:
        def extract_text(self):
            return "고객님의 전체 담보 진단 현황\n상해사망 2억 1억 1,700만 -8,300만 부족"

        def extract_tables(self):
            return [table]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(coverage_analysis_parser.pdfplumber, "open", lambda _path: FakePdf())
    candidates = detect_candidate_pages("legacy.pdf", DocumentType.COVERAGE_ANALYSIS_SHORT)
    assert candidates["category_detail_pages"] == [1]
    rows = parse_coverage_analysis("legacy.pdf", candidates)
    assert [(row.coverage_name, row.amount, row.source_page) for row in rows] == [
        ("상해사망", 117_000_000, 1),
        ("질병사망", 0, 1),
    ]


class TestCoverageAnalysisParser:
    @pytest.mark.parametrize("alias", ["coverage_case_01", "coverage_case_05", "coverage_case_02", "coverage_case_03"])
    def test_private_real_pdf_parser_returns_full_catalog(self, alias):
        pdf_path = private_pdf(alias)
        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
        coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

        # 보장별 집계 parser는 회사별 원장이 아니라 고정 50개 보장 카탈로그를 반환한다.
        assert len(coverages) == 50
