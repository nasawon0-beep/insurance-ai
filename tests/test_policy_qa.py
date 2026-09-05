import pytest

from database import policy_qa


def _patch_sources(monkeypatch, policies=None, consultations=None):
    monkeypatch.setattr(policy_qa.repo, "list_policies", lambda conn, cid: policies or [])
    monkeypatch.setattr(policy_qa.repo, "list_consultations", lambda conn, cid: consultations or [])


def test_contract_summary_fallback(monkeypatch):
    _patch_sources(monkeypatch, policies=[{"insurer": "한빛생명", "product_name": "든든보험"}])
    monkeypatch.setattr(policy_qa, "_call_llm", lambda prompt, model: {"answer": "계약 요약 답변"})
    result = policy_qa.fallback_answer(object(), "c1", "가입 상품은 무엇인가요?")
    assert result["basis"] == "policy_summary"
    assert result["fallback"] is True
    assert result["abstained"] is False
    assert result["disclaimer"]


def test_no_sources_abstains(monkeypatch):
    _patch_sources(monkeypatch)
    result = policy_qa.fallback_answer(object(), "c1", "가입 상품은 무엇인가요?")
    assert result["basis"] == "none"
    assert result["abstained"] is True


def test_high_risk_abstains_even_with_contract(monkeypatch):
    _patch_sources(monkeypatch, policies=[{"insurer": "한빛생명"}])
    result = policy_qa.fallback_answer(object(), "c1", "입원비 지급되나요")
    assert result["basis"] == "none"
    assert result["abstained"] is True


@pytest.mark.parametrize("question", [
    "입원하면 얼마나 나와?", "진단받으면 얼마 나와요?", "이거 탈 수 있어?",
    "수령 가능한가요?", "입원비 나오나요?",
])
def test_common_payout_wording_abstains_without_llm(monkeypatch, question):
    _patch_sources(monkeypatch, policies=[{"insurer": "한빛생명"}])
    monkeypatch.setattr(
        policy_qa, "_call_llm",
        lambda prompt, model: pytest.fail("high-risk question must not call LLM"),
    )
    result = policy_qa.fallback_answer(object(), "c1", question)
    assert result["basis"] == "none"
    assert result["abstained"] is True


def test_prompt_excludes_transcript_and_labels_old_coverage(monkeypatch):
    _patch_sources(
        monkeypatch,
        policies=[{"insurer": "한빛생명", "product_name": "든든보험"}],
        consultations=[{"coverage_json": "암진단비 1천만원 / 901201-1234567", "transcript": "비밀녹취문자열"}],
    )
    captured = []
    monkeypatch.setattr(
        policy_qa, "_call_llm",
        lambda prompt, model: captured.append(prompt) or {"answer": "참고 답변"},
    )
    policy_qa.fallback_answer(object(), "c1", "가입 내용 요약해줘")
    assert "[과거 상담 시점 보장현황]" in captured[0]
    assert "암진단비 1천만원" in captured[0]
    assert "901201-1234567" not in captured[0]
    assert "******-*******" in captured[0]
    assert "비밀녹취문자열" not in captured[0]
    assert "rrn" not in captured[0].lower()


def test_coverage_only_fallback_uses_policy_summary_basis(monkeypatch):
    _patch_sources(monkeypatch, consultations=[{"coverage_json": "가입 현황 요약"}])
    monkeypatch.setattr(policy_qa, "_call_llm", lambda prompt, model: {"answer": "참고 답변"})
    result = policy_qa.fallback_answer(object(), "c1", "가입 내용 요약해줘")
    assert result["basis"] == "policy_summary"


def test_fallback_forces_grounded_false(monkeypatch):
    _patch_sources(monkeypatch, policies=[{"insurer": "한빛생명"}])
    monkeypatch.setattr(
        policy_qa, "_call_llm", lambda prompt, model: {"answer": "참고 답변", "grounded": True},
    )
    result = policy_qa.fallback_answer(object(), "c1", "가입 내용 요약해줘")
    assert result["grounded"] is False
