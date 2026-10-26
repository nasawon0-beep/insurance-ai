from database.policy_coverages import (
    DocumentType,
    classify_document_type,
    detect_candidate_pages,
)
from pdf_fixture_resolver import private_pdf


def test_detects_credit_bureau_contract_ledger_by_structural_headers(monkeypatch):
    class FakePage:
        def extract_text(self):
            return (
                "신정원 담보명 회사명 상품명 회사 담보명 "
                "가입금액 보험시기 보험종기"
            )

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _path: FakePdf())
    pages = detect_candidate_pages(
        "synthetic-credit-bureau.pdf", DocumentType.COVERAGE_ANALYSIS_FULL
    )
    assert pages["detail_list_pages"] == [1]


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
