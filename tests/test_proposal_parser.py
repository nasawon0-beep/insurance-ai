from database.proposal_parser import parse_proposal
from database.policy_coverages import detect_candidate_pages, DocumentType
from private_pdf_fixtures import private_pdf

class TestProposalParser:

    def test_parse_private_proposal_case_01(self):
        pdf_path = private_pdf("proposal_case_01")

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.PROPOSAL)
        proposal_info = parse_proposal(str(pdf_path), candidate_pages)

        assert proposal_info.insurer != ""
        assert proposal_info.product_name != ""
        assert proposal_info.first_premium == 48360

        # 검수/저장 경로가 원본 표 전체를 보존하므로 10행 절단을 허용하지 않는다.
        assert len(proposal_info.riders) == 16

        for rider in proposal_info.riders:
            assert rider.rider_name != ""
            assert rider.coverage_amount > 0

    def test_parse_private_proposal_case_02(self):
        pdf_path = private_pdf("proposal_case_02")

        candidate_pages = detect_candidate_pages(str(pdf_path), DocumentType.PROPOSAL)
        proposal_info = parse_proposal(str(pdf_path), candidate_pages)

        assert proposal_info.insurer != ""
        assert proposal_info.product_name != ""
        assert proposal_info.first_premium == 145900
        assert len(proposal_info.riders) == 25

        for rider in proposal_info.riders:
            assert rider.rider_name != ""
            assert rider.coverage_amount > 0
