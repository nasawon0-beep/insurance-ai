"""약관 근거가 없을 때 계약 요약만 사용하는 저신뢰 답변 경로."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

from . import repo
from .assistant import KEEP_ALIVE, LLM_MODEL, OLLAMA_BASE

DISCLAIMER = "이 답변은 약관이 아니라 계약 요약 정보(보험사·상품·플랜·보험료)만 보고 만든 참고용 추정입니다. 실제 보장 여부와 지급 금액은 반드시 약관 원문으로 확인하세요."
_HIGH_RISK = (
    "지급", "보상", "보험금", "면책", "감액", "환급", "청구", "얼마 받", "받을 수 있",
    "보장금액", "보장 금액", "얼마나 나와", "얼마 나와", "얼마나 나옴", "나오나요",
    "나와요", "나옵니까", "수령", "탈 수 있", "받나요", "받을수",
)
_CONNECT_NOTICE = "이 질문은 약관 확인이 필요합니다. 계약에 약관 PDF 를 연결해 주세요."
_RRN13_RE = re.compile(r"\d{6}[-\s]?\d{7}")


def _is_high_risk(q: str) -> bool:
    return any(keyword in (q or "") for keyword in _HIGH_RISK)


def _call_llm(prompt: str, model: str) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_predict": 400, "num_ctx": 4096},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        parsed = json.loads((data.get("response") or "").strip())
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _result(question: str, answer: str, *, basis: str, abstained: bool, fallback: bool,
            document_ids: Optional[list[str]] = None, disclaimer: Optional[str] = None) -> dict:
    return {
        "question": question, "answer": answer, "company": None, "product": None,
        "clause": None, "page": None, "pages": [], "grounded": False,
        "abstained": abstained, "sources": [], "document_ids": document_ids or [],
        "basis": basis, "fallback": fallback, "disclaimer": disclaimer,
    }


def fallback_answer(conn, customer_id: str, question: str, model: Optional[str] = None) -> dict:
    policies = repo.list_policies(conn, customer_id)
    policy_lines = []
    for p in policies:
        premium = p.get("premium")
        premium_text = f"{premium:,}원" if isinstance(premium, (int, float)) else "미상"
        policy_lines.append(_RRN13_RE.sub(
            "******-*******",
            "- " + " / ".join([
                p.get("insurer") or "보험사 미상", p.get("product_name") or "상품 미상",
                f"플랜 {p.get('plan_type') or '미상'}", f"보험료 {premium_text} ({p.get('payment_cycle') or '주기 미상'})",
                f"보험기간 {p.get('insured_period') or '미상'}", f"납입기간 {p.get('payment_period') or '미상'}",
                f"상태 {p.get('status') or '미상'}", f"만기 {p.get('end_date') or '미상'}",
            ]),
        ))

    coverage = ""
    for consultation in repo.list_consultations(conn, customer_id):
        value = str(consultation.get("coverage_json") or "").strip()
        if value:
            coverage = _RRN13_RE.sub("******-*******", value[:2000])
            break

    if _is_high_risk(question):
        return _result(question, _CONNECT_NOTICE, basis="none", abstained=True, fallback=False)
    if not policy_lines and not coverage:
        return _result(
            question,
            "이 고객에게 연결된 약관이 없습니다. 계약에 약관 PDF 를 연결해 주세요.",
            basis="none", abstained=True, fallback=False,
        )

    prompt = (
        "약관이 없습니다. 아래 [계약 요약]과 [과거 상담 시점 보장현황]만 보고 답하세요. "
        "보장 금액·지급 여부를 단정하지 마세요. 요약에 없으면 '약관 확인이 필요합니다'라고 답하세요. "
        "과거 상담 시점의 보장현황은 최신 계약과 다를 수 있습니다. 한국어 2~3문장으로 답하고 "
        "JSON {\"answer\": \"답변\", \"grounded\": false} 하나만 출력하세요."
        f"\n\n[계약 요약]\n{chr(10).join(policy_lines) or '(없음)'}"
        f"\n\n[과거 상담 시점 보장현황]\n{coverage or '(없음)'}"
        f"\n\n[질문]\n{question}"
    )
    selected_model = model or os.environ.get("POLICY_QA_MODEL", LLM_MODEL)
    data = _call_llm(prompt, selected_model)
    answer = str(data.get("answer") or "약관 확인이 필요합니다.")
    result = _result(question, answer, basis="policy_summary", abstained=False,
                     fallback=True, disclaimer=DISCLAIMER)
    if len(policies) == 1:
        result["company"] = policies[0].get("insurer")
        result["product"] = policies[0].get("product_name")
    return result
