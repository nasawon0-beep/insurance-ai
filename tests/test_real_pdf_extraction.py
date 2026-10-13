import json
from pathlib import Path

import pytest

from database import policy_coverages
from parser.doc_parser import parse_pdf as parse_document_pdf


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
    coverages = policy_coverages.parse_policy_coverages_from_pages(
        pages,
        source_file_name=doc.filename,
        source_hash=doc.doc_id,
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
        ("filename", "expected_key"),
        [
            ("이일휴_260905.pdf", "이일휴_260905"),
            ("나상원_261012.pdf", "나상원_261012"),
            ("정인성_261012.pdf", "정인성_261012"),
            ("정병용_260923.pdf", "정병용_260923"),
            ("김민주_261012.pdf", "김민주_261012"),
        ],
    )
    def test_coverage_analysis_pdfs(self, expected_results, filename, expected_key):
        pdf_path = FIXTURES_DIR / "coverage_analysis" / filename
        expected = expected_results["coverage_analysis"][expected_key]

        result = _parse_policy_coverages(pdf_path)

        assert result["page_count"] == expected["pages"]
        coverages = result["coverages"]
        if "expected_coverages" in expected:
            assert len(coverages) == expected["expected_coverages"]
            assert _misc_ratio(coverages) <= expected["expected_misc_ratio"]
        else:
            assert expected["expected_coverages_min"] <= len(coverages) <= expected["expected_coverages_max"]
            assert _misc_ratio(coverages) <= expected["expected_misc_ratio_max"]

    @pytest.mark.parametrize(
        ("filename", "expected_key"),
        [
            ("박순남_암승인_261012.pdf", "박순남_암승인_261012"),
            ("박순남_수정_261012.pdf", "박순남_수정_261012"),
        ],
    )
    def test_proposal_pdfs(self, expected_results, filename, expected_key):
        pdf_path = FIXTURES_DIR / "proposal" / filename
        expected = expected_results["proposal"][expected_key]

        result = _parse_policy_coverages(pdf_path)

        assert result["page_count"] == expected["pages"]
        assert expected["expected_riders_min"] <= len(result["coverages"]) <= expected["expected_riders_max"]
