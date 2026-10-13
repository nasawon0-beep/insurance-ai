import pytest
from pathlib import Path

from database.policy_coverages import (
    DocumentType,
    classify_document_type,
    detect_candidate_pages,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_pdfs"


@pytest.mark.skipif(
    not FIXTURES_DIR.exists(),
    reason="Real PDF fixtures not available",
)
class TestDocumentClassifier:

    def test_classify_이일휴_coverage_analysis(self):
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "이일휴_260905.pdf"
        doc_type, metadata = classify_document_type(str(pdf_path))

        assert doc_type in [
            DocumentType.COVERAGE_ANALYSIS_FULL,
            DocumentType.COVERAGE_ANALYSIS_SHORT,
        ]
        assert metadata["page_count"] == 20

    def test_classify_박순남_proposal(self):
        pdf_path = FIXTURES_DIR / "proposal" / "박순남_암승인_261012.pdf"
        doc_type, metadata = classify_document_type(str(pdf_path))

        assert doc_type == DocumentType.PROPOSAL
        assert metadata["has_proposal_table"] is True

    def test_detect_pages_김민주(self):
        pdf_path = FIXTURES_DIR / "coverage_analysis" / "김민주_261012.pdf"
        pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)

        # 41페이지 문서이므로 여러 섹션 페이지가 있어야 함
        assert len(pages["coverage_summary_pages"]) >= 1
        assert len(pages["detail_list_pages"]) >= 1
