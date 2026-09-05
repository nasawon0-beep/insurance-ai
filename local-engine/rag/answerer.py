"""
답변 생성 (작업 E, 3단계).

검색된 조각 + 질문을 STANDARD 티어 모델(qwen2.5:14b)에게 넘겨
근거와 함께 답을 만든다. 계약(문서 9번): 응답에 항상
{answer, company, product, clause, page} 키가 있어야 한다.

환각 방지 2중 장치:
  1) 검색 점수가 바닥이면(MIN_SCORE 미만) 모델을 아예 부르지 않고 기권.
  2) 모델이 발췌문에서 근거를 못 찾으면 grounded=false 로 답하게 하고,
     그 경우 abstained=true 로 표시한다.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .pipeline import search

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("RAG_LLM_MODEL", "qwen2.5:14b")
MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0.1"))

ABSTAIN_TEXT = "해당 약관에서 지급 여부를 확정할 근거를 찾지 못했습니다."

SYSTEM_PROMPT = """당신은 보험 약관 전문 AI입니다. 반드시 아래 규칙을 지키세요.

1. 오직 [약관 발췌문]에 있는 내용만 근거로 답하세요. 발췌문에 없는 내용은 추측하지 마세요.
2. 발췌문에서 답의 근거를 찾지 못하면 grounded 를 false 로 두고,
   answer 에는 "{abstain}" 라고 쓰세요.
3. 반드시 아래 JSON 형식 하나만 출력하세요. 앞뒤에 다른 말을 붙이지 마세요.

{{
  "answer": "핵심 답변 (한국어, 2~4문장)",
  "company": "보험사명 또는 null",
  "product": "상품명 또는 null",
  "clause": "근거가 된 조항 번호/제목 또는 null",
  "grounded": true 또는 false
}}""".format(abstain=ABSTAIN_TEXT)


def _format_context(hits: list[dict]) -> str:
    return "\n\n".join(
        f"[출처: {h['filename']} p.{h['page']}]\n{h['text']}" for h in hits
    )


def _call_llm(model: str, question: str, context: str) -> dict:
    prompt = (
        f"{SYSTEM_PROMPT}\n\n[약관 발췌문]\n{context}\n\n[질문]\n{question}\n\n[출력 JSON]"
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
        "options": {"temperature": 0, "num_predict": 700},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = (data.get("response") or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 모델이 JSON을 안 지켰을 때: 원문을 answer로, grounded는 보수적으로 판단
        return {
            "answer": raw or ABSTAIN_TEXT,
            "company": None,
            "product": None,
            "clause": None,
            "grounded": bool(raw),
        }


def answer(
    question: str,
    top_k: int = 4,
    doc_id: str | None = None,
    doc_ids: list[str] | None = None,
    db_path: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    model = model or LLM_MODEL
    retrieval = search(
        question, top_k=top_k, doc_id=doc_id, doc_ids=doc_ids, db_path=db_path
    )
    hits = retrieval["hits"]
    used = [h for h in hits if h["score"] >= MIN_SCORE]

    base: dict[str, Any] = {
        "question": question,
        "embed_model": retrieval["embed_model"],
        "model": model,
    }

    if not used:
        return {
            **base,
            "answer": ABSTAIN_TEXT,
            "company": None,
            "product": None,
            "clause": None,
            "page": None,
            "pages": [],
            "grounded": False,
            "abstained": True,
            "sources": hits,
        }

    llm = _call_llm(model, question, _format_context(used))
    grounded = bool(llm.get("grounded", True))

    # page 는 가장 관련 높은 근거 조각의 페이지. pages 는 쓰인 근거들의 페이지(순위순, 중복 제거).
    # 모델이 근거 없다고 판단하면(grounded=false) page 를 비운다 — 기권 답변 옆에
    # 구체적 페이지가 붙어 오해를 주지 않도록 (문서 9번). 무엇을 봤는지는 sources 에 남긴다.
    pages: list[int] = []
    for h in used:  # used 는 search 가 점수 내림차순으로 준 순서 그대로
        if h["page"] not in pages:
            pages.append(h["page"])

    return {
        **base,
        "answer": llm.get("answer") or ABSTAIN_TEXT,
        "company": llm.get("company") if grounded else None,
        "product": llm.get("product") if grounded else None,
        "clause": llm.get("clause") if grounded else None,
        "page": pages[0] if grounded else None,
        "pages": pages if grounded else [],
        "grounded": grounded,
        "abstained": not grounded,
        "sources": used,
    }
