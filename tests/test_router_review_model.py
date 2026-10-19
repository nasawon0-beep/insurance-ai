import pytest
from fastapi.testclient import TestClient
from private_pdf_fixtures import private_pdf


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "customers.sqlite3"))
    import main

    return TestClient(main.app)


class TestRouterReviewModel:
    def test_proposal_review_model(self, client):
        """가입제안서 review model"""
        proposal_pdf = private_pdf("proposal_case_01")

        with proposal_pdf.open("rb") as f:
            response = client.post(
                "/capture",
                files={"file": ("private-proposal.pdf", f, "application/pdf")},
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
        coverage_pdf = private_pdf("coverage_case_05")

        with coverage_pdf.open("rb") as f:
            response = client.post(
                "/capture",
                files={"file": ("private-coverage-analysis.pdf", f, "application/pdf")},
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
        contract_rows = item["policy_coverages"]
        assert contract_rows
        assert all(row.get("insurer") not in {"통합보장분석", "알 수 없음"} for row in contract_rows)
        assert all(row.get("source_page") for row in contract_rows)
        # 검토용 표준항목 집계와 저장용 계약별 원장은 서로 다른 배열이어야 한다.
        assert len(contract_rows) != len(review["coverages"])
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
