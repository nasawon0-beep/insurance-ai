import pytest
from pathlib import Path

from database.proposal_parser import parse_proposal
from database.policy_coverages import detect_candidate_pages, DocumentType

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_pdfs"


@pytest.mark.skipif(
    not FIXTURES_DIR.exists(),
    reason="Real PDF fixtures not available",
)
class TestProposalParser:

    def test_parse_박순남_암승인(self):
        """박순남 암 승인 가입제안서 (5p)"""
        pdf_path = FIXTURES_DIR / "proposal" / "박순남_암승인_261012.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.PROPOSAL)
        proposal_info = parse_proposal(str(pdf_path), candidate_pages)

        assert proposal_info.insurer != ""
        assert proposal_info.product_name != ""
        assert proposal_info.first_premium == 48360

        assert 8 <= len(proposal_info.riders) <= 10

        for rider in proposal_info.riders:
            assert rider.rider_name != ""
            assert rider.coverage_amount > 0

    def test_parse_박순남_수정(self):
        """박순남 수정 가입제안서 (8p)"""
        pdf_path = FIXTURES_DIR / "proposal" / "박순남_수정_261012.pdf"

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.PROPOSAL)
        proposal_info = parse_proposal(str(pdf_path), candidate_pages)

        assert proposal_info.insurer != ""
        assert proposal_info.product_name != ""
        assert proposal_info.first_premium == 145900
        assert 15 <= len(proposal_info.riders) <= 20

        for rider in proposal_info.riders:
            assert rider.rider_name != ""
            assert rider.coverage_amount > 0
