import pytest
from pathlib import Path

from database.coverage_analysis_parser import parse_coverage_analysis
from database.policy_coverages import DocumentType, detect_candidate_pages

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_pdfs"


@pytest.mark.skipif(
    not FIXTURES_DIR.exists(),
    reason="Real PDF fixtures not available",
)
class TestCoverageAnalysisParser:
    def test_parse_이일휴(self):
        """이일휴 M보장분석 (20p, 짧은 양식)"""
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "이일휴_260905.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
        coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

        assert len(coverages) == 50

    def test_parse_김민주(self):
        """김민주 M보장분석 (41p, 긴 양식)"""
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "김민주_261012.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
        coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

        assert 73 <= len(coverages) <= 85

    def test_parse_나상원(self):
        """나상원 M보장분석 (32p, 긴 양식)"""
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "나상원_261012.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
        coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

        assert 70 <= len(coverages) <= 85

    def test_parse_정인성(self):
        """정인성 M보장분석 (29p, 긴 양식)"""
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "정인성_261012.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)
        coverages = parse_coverage_analysis(str(pdf_path), candidate_pages)

        assert 38 <= len(coverages) <= 45
