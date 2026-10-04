"""
AI 문의 (자연어 CRM 질의) 테스트.

LLM 호출(assistant._call_llm)은 전부 목으로 대체한다 — 느리고 비결정적이라.
컨텍스트 조립·집계·이름 매핑·폴백만 결정적으로 검증한다.
"""
import base64
import json
import re

import pytest

pytest.importorskip("httpx")

_TEST_KEY_B64 = base64.b64encode(b"0" * 32).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from database import crypto

    monkeypatch.setenv("CUSTOMER_DB_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CUSTOMER_DB_KEY_B64", _TEST_KEY_B64)
    crypto.reset_cache()
    from fastapi.testclient import TestClient
    import main

    yield TestClient(main.app)
    crypto.reset_cache()


def _conn():
    from database.db import connect, init_schema

    conn = connect()
    init_schema(conn)
    return conn


def _seed_customer(client, **kw):
    body = {"name": "홍길동", **kw}
    r = client.post("/customers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_policy(client, cid, **kw):
    r = client.post(f"/customers/{cid}/policies", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 컨텍스트 조립 ----------

def test_build_context_excludes_rrn(client):
    from database import assistant

    c = _seed_customer(
        client,
        name="이영희",
        address="서울시 강남구 테헤란로 123",
        memo="콜드콜에서 만남, 암보험 관심",
        birth_date="1990-01-01",
    )
    # rrn 은 별도 PATCH (본 payload 와 분리 — 422 회귀 방지 원칙)
    assert client.patch(
        f"/customers/{c['id']}", json={"rrn": "900101-1234567"}
    ).status_code == 200

    conn = _conn()
    try:
        ctx = assistant.build_context(conn)
    finally:
        conn.close()

    # 주민번호(원문·13자리)는 컨텍스트 어디에도 없어야 한다
    assert "900101-1234567" not in ctx
    assert "9001011234567" not in ctx
    assert "1234567" not in ctx
    assert re.search(r"\d{6}[-\s]?\d{7}", ctx) is None
    # 주소·메모는 들어간다
    assert "서울시 강남구 테헤란로 123" in ctx
    assert "콜드콜에서 만남" in ctx


def test_aggregate_premium_sum_divides_yearly(client):
    from database import assistant

    c = _seed_customer(client, name="최유진")
    _seed_policy(client, c["id"], insurer="한화생명", product_name="월납상품",
                 premium=45000, payment_cycle="MONTHLY", status="ACTIVE")
    _seed_policy(client, c["id"], insurer="삼성화재", product_name="연납상품",
                 premium=120000, payment_cycle="YEARLY", status="ACTIVE")
    _seed_policy(client, c["id"], insurer="교보생명", product_name="일시납상품",
                 premium=5000000, payment_cycle="일시납", status="ACTIVE")
    _seed_policy(client, c["id"], insurer="현대해상", product_name="실효상품",
                 premium=99999, payment_cycle="MONTHLY", status="LAPSED")

    conn = _conn()
    try:
        hints = assistant._aggregate_hints(conn)
    finally:
        conn.close()

    # 45,000 + 120,000/12(=10,000) = 55,000 (일시납·비활성 제외)
    assert "55,000원" in hints
    assert "5,000,000" not in hints
    assert "5,055,000" not in hints


# ---------- answer() ----------

def _mock_llm(monkeypatch, payload, capture=None):
    from database import assistant

    def fake(prompt, model):
        if capture is not None:
            capture.append(prompt)
        return payload

    monkeypatch.setattr(assistant, "_call_llm", fake)


def test_answer_maps_used_customers(client, monkeypatch):
    seeded = _seed_customer(client, name="이영희")
    _mock_llm(monkeypatch, {
        "answer": "이영희 고객의 월 보험료는 45,000원입니다.",
        "used_customer_names": ["이영희"],
        "grounded": True,
        "no_data": False,
    })
    r = client.post("/assistant/ask", json={"question": "이영희 보험료 얼마 내?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_customers"][0]["name"] == "이영희"
    assert body["used_customers"][0]["id"] == seeded["id"]
    assert body["no_data"] is False
    assert body["grounded"] is True
    assert body["data_scope"] == "filtered"


def test_answer_no_data_clears_customers(client, monkeypatch):
    _seed_customer(client, name="이영희")
    _mock_llm(monkeypatch, {
        "answer": "해당 정보를 찾지 못했습니다.",
        "used_customer_names": ["이영희"],
        "grounded": False,
        "no_data": True,
    })
    r = client.post("/assistant/ask", json={"question": "김에이비씨 보험료?"})
    assert r.status_code == 200
    body = r.json()
    assert body["no_data"] is True
    assert body["used_customers"] == []


def test_answer_nonjson_fallback(client, monkeypatch):
    _seed_customer(client, name="이영희")
    _mock_llm(monkeypatch, {})  # JSON 파싱 실패 시뮬
    r = client.post("/assistant/ask", json={"question": "아무거나"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "답변을 생성하지 못했습니다."
    assert body["used_customers"] == []
    assert body["grounded"] is False
    assert body["no_data"] is False


def test_history_included_in_prompt(client, monkeypatch):
    _seed_customer(client, name="이영희")
    captured: list[str] = []
    _mock_llm(
        monkeypatch,
        {"answer": "네.", "used_customer_names": [], "grounded": True, "no_data": False},
        capture=captured,
    )
    r = client.post("/assistant/ask", json={
        "question": "그럼 만기는?",
        "history": [
            {"role": "user", "content": "이전질문XYZ"},
            {"role": "assistant", "content": "이전답변ABC"},
        ],
    })
    assert r.status_code == 200
    assert len(captured) == 1
    prompt = captured[0]
    assert "[이전 대화]" in prompt
    assert "사용자: 이전질문XYZ" in prompt
    assert "AI: 이전답변ABC" in prompt


def test_answer_filters_named_customer_and_keeps_roster(client, monkeypatch):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="박영희")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "네.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    body = client.post("/assistant/ask", json={"question": "김철수 연락처 알려줘"}).json()
    customer_block = captured[0].split("[고객 데이터]\n", 1)[1].split("[전체 고객 명단]", 1)[0]
    assert "■ 김철수" in customer_block
    assert "■ 박영희" not in customer_block
    assert "[전체 고객 명단]" in captured[0]
    assert body["data_scope"] == "filtered"


@pytest.mark.parametrize("question", ["김철수 계약 목록", "김철수 총 보험료"])
def test_exact_name_overrides_broad_wording(client, monkeypatch, question):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="박영희")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "네.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    body = client.post("/assistant/ask", json={"question": question}).json()
    assert captured == []
    assert body["routing_type"] == "db_query"
    assert body["used_customers"][0]["name"] == "김철수"
    assert body["data_scope"] == "filtered"


def test_global_question_keeps_all_customers(client, monkeypatch):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="박영희")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "2명입니다.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    body = client.post("/assistant/ask", json={"question": "전체 고객 몇 명"}).json()
    assert "■ 김철수" in captured[0] and "■ 박영희" in captured[0]
    assert body["data_scope"] == "all"


def test_exact_full_name_precedes_suffix_match(client, monkeypatch):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="이철수")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "네.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    body = client.post("/assistant/ask", json={"question": "김철수 보험료"}).json()
    assert captured == []
    assert body["routing_type"] == "db_query"
    assert body["used_customers"][0]["name"] == "김철수"
    assert body["data_scope"] == "filtered"


def test_suffix_match_used_only_without_exact_name(client, monkeypatch):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="이철수")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "네.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    body = client.post("/assistant/ask", json={"question": "철수 계약"}).json()
    customer_block = captured[0].split("[고객 데이터]\n", 1)[1].split("[전체 고객 명단]", 1)[0]
    assert "■ 김철수" in customer_block and "■ 이철수" in customer_block
    assert body["data_scope"] == "filtered"


def test_answer_uses_customer_named_in_history(client, monkeypatch):
    _seed_customer(client, name="김철수")
    _seed_customer(client, name="박영희")
    captured: list[str] = []
    _mock_llm(monkeypatch, {"answer": "네.", "used_customer_names": [],
              "grounded": True, "no_data": False}, capture=captured)
    client.post("/assistant/ask", json={
        "question": "그 고객 만기는?",
        "history": [{"role": "user", "content": "김철수 보험 알려줘"}],
    })
    assert captured == []


def test_answer_filters_to_own_policies_when_asked(client, monkeypatch):
    c = _seed_customer(client, name="최유진")
    _seed_policy(client, c["id"], insurer="한화생명", product_name="내가판매한상품",
                 premium=45000, is_own=True)
    _seed_policy(client, c["id"], insurer="삼성화재", product_name="타사이관상품",
                 premium=30000, is_own=False)

    captured: list[str] = []
    _mock_llm(
        monkeypatch,
        {"answer": "내가판매한상품 하나입니다.", "used_customer_names": ["최유진"],
         "grounded": True, "no_data": False},
        capture=captured,
    )
    r = client.post("/assistant/ask", json={"question": "최유진 내가 가입시킨거 뭐야"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["own_only"] is True

    prompt = captured[0]
    # 내 계약만 컨텍스트에 남고, 타사 계약은 빠진다 + 지시문이 붙는다
    assert "내가판매한상품" in prompt
    assert "타사이관상품" not in prompt
    assert "본인이 직접 가입시킨 계약만" in prompt


def test_own_regex_does_not_falsely_trigger_on_third_person_direct(monkeypatch):
    """'고객이 직접 가입한' 처럼 1인칭이 아닌 '직접' 은 내 계약 필터를 켜면 안 된다."""
    from database.assistant import _is_own_only_question

    assert _is_own_only_question("최유진 내가 가입시킨거 뭐야") is True
    assert _is_own_only_question("제가 직접 가입시킨 계약만 보여줘") is True
    assert _is_own_only_question("내가 판매한 상품 목록") is True
    assert _is_own_only_question("최유진이 직접 가입한 보험 뭐야") is False
    assert _is_own_only_question("고객이 직접 가입한 계약 알려줘") is False
    assert _is_own_only_question("최유진 보험료 얼마 내?") is False


def test_classify_question_routes_by_question_type():
    from database.assistant import _classify_question

    assert _classify_question("홍길동 보험료 얼마야?") == "db_query"
    assert _classify_question("암보험 약관 면책 조건 알려줘") == "rag"
    assert _classify_question("이 상담 내용 요약해줘") == "summarize"
    assert _classify_question("다음 상담 전략 추천해줘") == "complex"


def test_summarize_question_uses_light_model(client, monkeypatch):
    _seed_customer(client, name="이영희")
    captured: list[tuple[str, str]] = []

    def fake(prompt, model):
        captured.append((prompt, model))
        return {"answer": "요약했습니다.", "used_customer_names": [], "grounded": True, "no_data": False}

    from database import assistant
    monkeypatch.setattr(assistant, "_call_llm", fake)

    body = client.post("/assistant/ask", json={"question": "최근 상담 내용 요약해줘"}).json()
    assert body["routing_type"] == "summarize"
    assert body["model"] == "qwen2.5:3b"
    assert captured[0][1] == "qwen2.5:3b"


def test_rag_question_routes_to_rag_answer(client, monkeypatch):
    from database import assistant

    def fake_rag(question):
        return {
            "question": question,
            "answer": "약관 답변입니다.",
            "model": "qwen2.5:14b",
            "grounded": True,
            "abstained": False,
            "sources": [],
        }

    monkeypatch.setattr(assistant, "_rag_answer", fake_rag)
    body = client.post("/assistant/ask", json={"question": "이 약관의 가입조건 설명해줘"}).json()
    assert body["routing_type"] == "rag"
    assert body["model"] == "qwen2.5:14b"
    assert body["data_scope"] == "rag"
    assert body["answer"] == "약관 답변입니다."


def test_normal_question_keeps_all_policies_with_markers(client, monkeypatch):
    c = _seed_customer(client, name="최유진")
    _seed_policy(client, c["id"], insurer="한화생명", product_name="내가판매한상품", is_own=True)
    _seed_policy(client, c["id"], insurer="삼성화재", product_name="타사이관상품", is_own=False)

    captured: list[str] = []
    _mock_llm(
        monkeypatch,
        {"answer": "두 건입니다.", "used_customer_names": ["최유진"],
         "grounded": True, "no_data": False},
        capture=captured,
    )
    r = client.post("/assistant/ask", json={"question": "최유진 보험 뭐 있어?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["own_only"] is False

    prompt = captured[0]
    assert "내가판매한상품" in prompt and "타사이관상품" in prompt
    assert "[내 계약]" in prompt and "[타사/미확인]" in prompt
    assert "본인이 직접 가입시킨 계약만" not in prompt


def test_assistant_context_shows_policyholder(client, monkeypatch):
    """계약자 ≠ 피보험자 계약: 계약 라인에 '계약자: X' + 역질문용 요약 라인이 들어간다."""
    c = _seed_customer(client, name="안우성")
    _seed_policy(
        client, c["id"],
        insurer="NH농협생명", product_name="NH올원더풀간병안심요양보험",
        policyholder_name="손유진",
    )

    captured: list[str] = []
    _mock_llm(
        monkeypatch,
        {"answer": "네.", "used_customer_names": [], "grounded": True, "no_data": False},
        capture=captured,
    )
    r = client.post("/assistant/ask", json={"question": "손유진이 계약자인 계약 뭐야"})
    assert r.status_code == 200, r.text
    prompt = captured[0]
    assert "계약자: 손유진" in prompt
    assert "계약자 손유진 → 피보험자 안우성" in prompt


def test_assistant_ask_response_shape(client, monkeypatch):
    _mock_llm(monkeypatch, {
        "answer": "등록된 고객이 없습니다.",
        "used_customer_names": [],
        "grounded": True,
        "no_data": False,
    })
    body = client.post("/assistant/ask", json={"question": "고객 몇 명?"}).json()
    assert set(body) >= {
        "answer", "used_customers", "data_scope", "model", "no_data", "grounded"
    }
    assert isinstance(body["answer"], str)
    assert isinstance(body["used_customers"], list)
    assert isinstance(body["no_data"], bool)
    assert isinstance(body["grounded"], bool)


def test_assistant_ask_empty_question_422(client):
    assert client.post("/assistant/ask", json={"question": ""}).status_code == 422


def test_assistant_ask_blank_question_422(client):
    assert client.post("/assistant/ask", json={"question": "   "}).status_code == 422


def test_customer_db_coverage_question_skips_llm(client, monkeypatch):
    """고객 상세 DB 질문은 qwen 호출 없이 coverage_json으로 즉시 답한다."""
    from database import assistant

    c = _seed_customer(client, name="나상원")
    _seed_policy(
        client,
        c["id"],
        insurer="테스트생명",
        product_name="건강보험",
        premium=50000,
        payment_cycle="MONTHLY",
        status="ACTIVE",
    )
    r = client.post(f"/customers/{c['id']}/consultations", json={
        "title": "보장분석",
        "coverage_json": json.dumps([
            {"name": "뇌진단비", "status": "충분", "current": "3,000만원", "recommended": "2,000만원"},
            {"name": "암진단비", "status": "부족", "current": "1,000만원"},
        ], ensure_ascii=False),
    })
    assert r.status_code == 201, r.text

    def fail_llm(prompt, model):  # pragma: no cover - 호출되면 테스트 실패
        raise AssertionError("LLM must not be called for DB coverage questions")

    monkeypatch.setattr(assistant, "_call_llm", fail_llm)
    r = client.post("/assistant/ask", json={
        "question": "나상원 뇌진단비 얼마있어?",
        "customer_id": c["id"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "rule_based_db"
    assert body["grounded"] is True
    assert "뇌진단비" in body["answer"]
    assert "3,000만원" in body["answer"]


def test_customer_db_premium_question_skips_llm(client, monkeypatch):
    from database import assistant

    c = _seed_customer(client, name="나상원")
    _seed_policy(client, c["id"], premium=60000, payment_cycle="MONTHLY", status="ACTIVE")
    _seed_policy(client, c["id"], premium=120000, payment_cycle="YEARLY", status="ACTIVE")
    _seed_policy(client, c["id"], premium=99999, payment_cycle="MONTHLY", status="LAPSED")

    monkeypatch.setattr(
        assistant,
        "_call_llm",
        lambda prompt, model: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )
    r = client.post("/assistant/ask", json={
        "question": "나상원 보험료",
        "customer_id": c["id"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "rule_based_db"
    assert "70,000원" in body["answer"]
