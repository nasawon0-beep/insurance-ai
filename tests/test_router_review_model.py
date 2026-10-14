from pathlib import Path

import pytest
from fastapi.testclient import TestClient


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_pdfs"
PROPOSAL_PDF = FIXTURES_DIR / "proposal" / "박순남_암승인_261012.pdf"
COVERAGE_ANALYSIS_PDF = FIXTURES_DIR / "coverage_analysis" / "김민주_261012.pdf"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    import main

    return TestClient(main.app)


@pytest.mark.skipif(
    not FIXTURES_DIR.exists(),
    reason="Real PDF fixtures not available",
)
class TestRouterReviewModel:
    def test_proposal_review_model(self, client):
        """가입제안서 review model"""
        assert PROPOSAL_PDF.exists(), f"fixture not found: {PROPOSAL_PDF}"

        with PROPOSAL_PDF.open("rb") as f:
            response = client.post(
                "/capture",
                files={"file": (PROPOSAL_PDF.name, f, "application/pdf")},
            )

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        review = item.get("proposal_review") or item["coverage_review"]

        assert item["doc_type"] == "proposal"
        assert review["doc_type"] == "proposal"
        assert review["metadata"]["page_count"] >= 1
        assert "has_proposal_table" in review["metadata"]
        assert "riders" in review
        assert review["riders"]
        rider = review["riders"][0]
        assert {
            "rider_type",
            "rider_name",
            "coverage_period",
            "payment_period",
            "coverage_amount",
            "coverage_amount_text",
            "premium",
            "premium_text",
        }.issubset(rider)

    def test_coverage_analysis_review_model(self, client):
        """보장분석서 review model"""
        assert COVERAGE_ANALYSIS_PDF.exists(), f"fixture not found: {COVERAGE_ANALYSIS_PDF}"

        with COVERAGE_ANALYSIS_PDF.open("rb") as f:
            response = client.post(
                "/capture",
                files={"file": (COVERAGE_ANALYSIS_PDF.name, f, "application/pdf")},
            )

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        review = item["coverage_review"]

        assert review["doc_type"] == "coverage_analysis"
        assert review["metadata"]["page_count"] >= 1
        assert "is_short_form" in review["metadata"]
        assert "is_full_form" in review["metadata"]
        assert "candidate_pages" in review["metadata"]
        assert "coverages" in review
        assert review["coverages"]
        coverage = review["coverages"][0]
        assert {
            "insurer",
            "product_name",
            "coverage_name",
            "category",
            "coverage_period",
            "payment_period",
            "amount",
            "amount_text",
            "source_page",
            "source_type",
            "confidence",
        }.issubset(coverage)
