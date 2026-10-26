import json
from pathlib import Path

import pytest

from database import policy_coverages
from database.proposal_parser import parse_proposal
from database.policy_coverages import detect_candidate_pages, DocumentType
from parser.doc_parser import parse_pdf as parse_document_pdf
from pdf_fixture_resolver import private_pdf


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_pdfs"
EXPECTED_FILE = FIXTURES_DIR / "expected_results.json"


@pytest.fixture
def expected_results():
    with open(EXPECTED_FILE, encoding="utf-8") as f:
        return json.load(f)


def _parse_policy_coverages(pdf_path: Path):
    if not pdf_path.exists():
        pytest.skip(f"Real PDF fixture not available: {pdf_path.name}")

    doc = parse_document_pdf(pdf_path)
    pages = [(page.page, page.text) for page in doc.pages]
    coverages = policy_coverages.parse_policy_coverages_from_pdf(
        str(pdf_path), source_file_name=doc.filename, source_hash=doc.doc_id
    ) or policy_coverages.parse_policy_coverages_from_pages(
        pages, source_file_name=doc.filename, source_hash=doc.doc_id
    )
    return {"page_count": doc.page_count, "coverages": coverages}


def _misc_ratio(coverages):
    if not coverages:
        return 1.0
    return sum(1 for c in coverages if c.get("category") == "기타" or c.get("standard_name") == "기타") / len(coverages)


@pytest.mark.skipif(
    not EXPECTED_FILE.exists(),
    reason="Real PDF expected results not available (local only)",
)
class TestRealPdfExtraction:
    @pytest.mark.parametrize(
        "alias",
        [
            "coverage_case_01",
            "coverage_case_02",
            "coverage_case_03",
            "coverage_case_04",
            "coverage_case_05",
        ],
    )
    def test_coverage_analysis_pdfs(self, expected_results, alias):
        pdf_path = private_pdf(alias)
        expected = expected_results["coverage_analysis"][alias]

        result = _parse_policy_coverages(pdf_path)

        assert result["page_count"] == expected["pages"]
        coverages = result["coverages"]
        if "expected_coverages" in expected:
            assert len(coverages) == expected["expected_coverages"]
            # 원본 신정원 분류가 `기타`인 행을 회사명 키워드로 억지 분류하지 않는다.
            assert all(
                c.get("standard_name_raw") == "기타"
                for c in coverages if c.get("standard_name") == "기타"
            )
        else:
            assert expected["expected_coverages_min"] <= len(coverages) <= expected["expected_coverages_max"]
            assert all(
                c.get("standard_name_raw") == "기타"
                for c in coverages if c.get("standard_name") == "기타"
            )

    @pytest.mark.parametrize(
        "alias",
        [
            "proposal_case_01",
            "proposal_case_02",
        ],
    )
    def test_proposal_pdfs(self, expected_results, alias):
        pdf_path = private_pdf(alias)
        expected = expected_results["proposal"][alias]
        doc = parse_document_pdf(pdf_path)
        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.PROPOSAL)
        proposal = parse_proposal(str(pdf_path), candidate_pages)

        assert doc.page_count == expected["pages"]
        assert expected["expected_riders_min"] <= len(proposal.riders) <= expected["expected_riders_max"]
        assert all(rider.rider_name and rider.coverage_amount > 0 for rider in proposal.riders)
