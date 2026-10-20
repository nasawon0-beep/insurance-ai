from database.policy_coverages import (
    DocumentType,
    classify_document_type,
    detect_candidate_pages,
)
from private_pdf_fixtures import private_pdf

class TestDocumentClassifier:

    def test_classify_private_coverage_case(self):
        pdf_path = private_pdf("coverage_case_01")
        doc_type, metadata = classify_document_type(str(pdf_path))

        assert doc_type in [
            DocumentType.COVERAGE_ANALYSIS_FULL,
            DocumentType.COVERAGE_ANALYSIS_SHORT,
        ]
        assert metadata["page_count"] == 20

    def test_classify_private_proposal_case(self):
        pdf_path = private_pdf("proposal_case_01")
        doc_type, metadata = classify_document_type(str(pdf_path))

        assert doc_type == DocumentType.PROPOSAL
        assert metadata["has_proposal_table"] is True

    def test_detect_pages_private_coverage_case(self):
        pdf_path = private_pdf("coverage_case_05")
        pages = detect_candidate_pages(str(pdf_path), DocumentType.COVERAGE_ANALYSIS_FULL)

        # 41페이지 문서이므로 여러 섹션 페이지가 있어야 함
        assert len(pages["coverage_summary_pages"]) >= 1
        assert len(pages["detail_list_pages"]) >= 1
