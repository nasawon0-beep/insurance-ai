"""
보장 분석: 한 고객의 여러 보험계약 + 연결된 약관을 종합해
카테고리별 보장 충분/부족/중복을 AI가 정리한다.

- 계약 메타(보험사/상품/플랜/보험료) + (약관 첨부 시) RAG 로 뽑은 조항 근거를 함께 넘긴다.
- 약관이 없으면 상품명 기준의 개략 분석만 (evidence 없음).
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from typing import Any, Optional

from rag import search as rag_search

from . import repo

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("RAG_LLM_MODEL", "qwen2.5:7b")

# 표준 보장 카테고리 + 약관 검색용 질의 (속도 위해 핵심 5개로 압축)
_CATEGORIES = [
    ("암·2대진단", "암 뇌졸중 급성심근경색 진단비 보장"),
    ("입원", "입원 일당 입원비 하루 지급"),
    ("수술", "수술비 수술 급여 분류표"),
    ("실손의료", "실손 통원 입원 의료비 자기부담금"),
    ("사망·후유장해", "사망보험금 후유장해 지급률"),
]

_PROMPT = """당신은 보험 보장분석 전문가다. 아래 [보험계약]과 [약관 근거]만 보고
고객의 보장 상태를 카테고리별로 정리하라. 근거에 없는 수치는 지어내지 마라.

JSON 하나만 출력:
{
  "overall": "2~3문장 총평",
  "categories": [
    {"name": "카테고리명", "status": "충분|부족|없음|중복",
     "detail": "무엇이 어떻게 보장되는지 / 왜 부족·중복인지",
     "policies": ["관련 상품명", ...],
     "evidence_pages": [숫자, ...]}
  ],
  "gaps": ["보완이 필요한 점", ...],
  "overlaps": ["중복되어 조정 여지가 있는 점", ...],
  "recommendations": ["상담자가 제안할 것", ...]
}

[보험계약]
{policies}

[약관 근거]
{evidence}
"""


def _policies_block(policies: list[dict]) -> str:
    if not policies:
        return "(등록된 보험계약 없음)"
    lines = []
    for p in policies:
        prem = f"{p['premium']:,}원" if p.get("premium") else "-"
        lines.append(
            f"- {p.get('insurer') or '?'} / {p.get('product_name') or '?'} "
            f"/ 플랜:{p.get('plan_type') or '-'} / 월납:{prem} / 상태:{p.get('status')}"
            + ("  [약관 첨부됨]" if p.get("document_id") else "  [약관 없음]")
        )
    return "\n".join(lines)


def _gather_evidence(doc_ids: list[str]) -> tuple[str, list[dict]]:
    if not doc_ids:
        return "(첨부된 약관이 없어 조항 근거 없음 — 상품명 기준 개략 분석)", []

    seen: set[str] = set()
    sources: list[dict] = []
    blocks: list[str] = []
    for cat, query in _CATEGORIES:
        hits = rag_search(query, top_k=2, doc_ids=doc_ids)["hits"]
        cat_lines = []
        for h in hits:
            if h["score"] < 0.15 or h["chunk_id"] in seen:
                continue
            seen.add(h["chunk_id"])
            sources.append(h)
            cat_lines.append(f"  (p.{h['page']}) {h['text']}")
        if cat_lines:
            blocks.append(f"[{cat}]\n" + "\n".join(cat_lines))
    return ("\n\n".join(blocks) or "(관련 조항을 찾지 못함)"), sources


def _call_llm(prompt: str, model: str) -> dict:
    payload = {
        "model": model, "prompt": prompt, "stream": False,
        "format": "json",
        "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
        "options": {"temperature": 0, "num_predict": 1200},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return json.loads((data.get("response") or "").strip())
    except json.JSONDecodeError:
        return {}


def analyze(
    conn: sqlite3.Connection, customer_id: str, model: Optional[str] = None
) -> Optional[dict[str, Any]]:
    if repo.get_customer(conn, customer_id) is None:
        return None

    model = model or LLM_MODEL
    policies = repo.list_policies(conn, customer_id)
    doc_ids = repo.customer_document_ids(conn, customer_id)

    if not policies:
        return {
            "overall": "등록된 보험계약이 없어 보장 분석을 할 수 없습니다.",
            "categories": [], "gaps": [], "overlaps": [], "recommendations": [],
            "analyzed_policies": 0, "has_documents": False, "sources": [], "model": model,
        }

    evidence, sources = _gather_evidence(doc_ids)
    prompt = _PROMPT.replace("{policies}", _policies_block(policies)).replace(
        "{evidence}", evidence
    )
    out = _call_llm(prompt, model)

    return {
        "overall": out.get("overall") or "",
        "categories": out.get("categories") or [],
        "gaps": out.get("gaps") or [],
        "overlaps": out.get("overlaps") or [],
        "recommendations": out.get("recommendations") or [],
        "analyzed_policies": len(policies),
        "has_documents": bool(doc_ids),
        "sources": sources,
        "model": model,
    }
