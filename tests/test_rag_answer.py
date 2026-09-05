"""
작업 E (3단계) 계약 테스트: 검색 결과 + 질문 → 근거 있는 답변.

핵심 불변식 (문서 9번 "근거 없는 답변 금지"):
- 응답에 항상 {answer, company, product, clause, page} 키가 있다.
- 답의 page 는 가장 관련 높은 근거 조각의 페이지다 (모델이 지어낸 값이 아님).
- 근거가 약하면(검색 0건) 모델을 부르지 않고 기권한다.
- 모델이 grounded=false 로 답하면 abstained=true 로 표시된다.

step3 를 검색 품질에서 떼어내기 위해 answerer.search 를 목으로 고정한다.
LLM 호출(_call_llm)도 목으로 대체 — 느리고 비결정적이라.
"""
import pytest

from parser.doc_parser import parse_pdf_bytes
from rag import answerer, pipeline
from rag.embedder import HashingEmbedder

PAGES = [
    "General provisions. Contract between the company and the insured person.",
    "Hospitalization benefit. The company pays a daily amount for each night the insured stays in hospital due to illness.",
    "Surgery benefit. The company pays a fixed lump sum when the insured undergoes a covered surgical operation.",
    "Exclusions. The company does not pay for cosmetic procedures or self-inflicted injury.",
]

CONTRACT_KEYS = {"answer", "company", "product", "clause", "page"}


def _hit(page, text, score):
    return {
        "chunk_id": f"doc:{page}:0",
        "doc_id": "doc",
        "filename": "policy.pdf",
        "page": page,
        "text": text,
        "score": score,
    }


def _mock_search(monkeypatch, hits):
    monkeypatch.setattr(
        answerer,
        "search",
        lambda *a, **k: {"query": "q", "embed_model": "mock", "hits": hits},
    )


def test_grounded_answer_uses_top_source_page(monkeypatch):
    _mock_search(
        monkeypatch,
        [
            _hit(3, "Surgery benefit. Fixed lump sum for a covered surgical operation.", 0.82),
            _hit(4, "Exclusions. No payment for cosmetic procedures.", 0.21),
        ],
    )

    seen = {}

    def fake_llm(model, question, context):
        seen["context"] = context
        return {
            "answer": "정해진 수술을 받으면 정액을 지급합니다.",
            "company": None,
            "product": None,
            "clause": "Surgery benefit",
            "grounded": True,
        }

    monkeypatch.setattr(answerer, "_call_llm", fake_llm)

    res = answerer.answer("수술하면 보험금 나오나요?", top_k=3)
    assert CONTRACT_KEYS <= set(res)
    assert res["page"] == 3  # 최상위 근거 = p.3
    assert res["pages"] == [3, 4]
    assert res["grounded"] is True and res["abstained"] is False
    assert res["clause"] == "Surgery benefit"
    assert "p.3" in seen["context"] and "surgical operation" in seen["context"]


def test_abstains_when_no_retrieval(monkeypatch):
    _mock_search(monkeypatch, [])
    called = {"llm": False}
    monkeypatch.setattr(
        answerer, "_call_llm", lambda *a, **k: called.__setitem__("llm", True)
    )

    res = answerer.answer("아무거나", top_k=5)
    assert called["llm"] is False  # 모델을 아예 안 불렀다
    assert res["abstained"] is True and res["grounded"] is False
    assert res["page"] is None
    assert res["answer"] == answerer.ABSTAIN_TEXT
    assert CONTRACT_KEYS <= set(res)


def test_abstains_when_all_scores_below_threshold(monkeypatch):
    _mock_search(monkeypatch, [_hit(2, "관련 낮은 조각", 0.01)])
    monkeypatch.setattr(
        answerer, "_call_llm", lambda *a, **k: pytest.fail("불려선 안 됨")
    )
    res = answerer.answer("질문", top_k=3)
    assert res["abstained"] is True
    assert res["page"] is None


def test_model_says_ungrounded(monkeypatch):
    _mock_search(monkeypatch, [_hit(3, "Surgery benefit ...", 0.6)])
    monkeypatch.setattr(
        answerer,
        "_call_llm",
        lambda *a, **k: {"answer": answerer.ABSTAIN_TEXT, "grounded": False},
    )
    res = answerer.answer("화성 여행 중 사고도 보상되나요?", top_k=3)
    assert res["abstained"] is True and res["grounded"] is False
    assert CONTRACT_KEYS <= set(res)


def test_call_llm_handles_non_json_response(monkeypatch):
    """모델이 JSON 규칙을 안 지키고 자유 텍스트를 뱉어도 죽지 않는다."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json as _json

            return _json.dumps({"response": "그냥 줄글로 답해버린 경우"}).encode("utf-8")

    monkeypatch.setattr(answerer.urllib.request, "urlopen", lambda *a, **k: _Resp())

    out = answerer._call_llm("qwen2.5:14b", "질문", "context")
    assert out["answer"] == "그냥 줄글로 답해버린 경우"
    assert out["grounded"] is True
    assert set(out) >= {"answer", "company", "product", "clause", "grounded"}


def test_endpoint_ask_end_to_end(build_pdf, tmp_path, monkeypatch):
    """실제 색인 → /rag/ask 배선 확인. 임베더는 해싱, LLM만 목."""
    pytest.importorskip("httpx")
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "http.sqlite3"))
    monkeypatch.setattr(pipeline, "get_embedder", lambda: HashingEmbedder())
    monkeypatch.setattr(
        answerer,
        "_call_llm",
        lambda *a, **k: {
            "answer": "입원 1일당 정액을 지급합니다.",
            "company": None,
            "product": None,
            "clause": "Hospitalization benefit",
            "grounded": True,
        },
    )
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    parsed = client.post(
        "/parse/pdf", files={"file": ("policy.pdf", build_pdf(PAGES), "application/pdf")}
    ).json()
    client.post("/rag/index", json=parsed)

    r = client.get(
        "/rag/ask", params={"q": "hospital daily amount each night", "top_k": 3}
    )
    assert r.status_code == 200
    body = r.json()
    assert CONTRACT_KEYS <= set(body)
    assert body["page"] == 2  # 해싱 임베더로도 p.2 가 최상위
    assert body["clause"] == "Hospitalization benefit"
    assert body["sources"][0]["page"] == 2
