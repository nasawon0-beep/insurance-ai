"""
rag 라우터: 색인 / 검색 엔드포인트 (작업 E, 1~2단계).

  POST /rag/index    body = parser의 /parse/pdf 응답(ParsedDoc) 그대로
  GET  /rag/search   ?q=...&top_k=5&doc_id=(선택)   → 근거 조각 + 페이지만
  GET  /rag/ask      ?q=...&top_k=5&doc_id=(선택)   → 근거 + AI 답변 (작업 E 3단계)
  GET  /rag/docs     현재 색인된 문서 목록
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .answerer import answer
from .pipeline import index_parsed_doc, list_docs, search

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/index")
def rag_index(doc: dict = Body(..., description="ParsedDoc (/parse/pdf 응답)")):
    if not isinstance(doc, dict) or "doc_id" not in doc or "pages" not in doc:
        raise HTTPException(status_code=422, detail="ParsedDoc 형식이 아닙니다 (doc_id, pages 필요).")
    return index_parsed_doc(doc)


@router.get("/search")
def rag_search(
    q: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=20),
    doc_id: Optional[str] = Query(None),
):
    return search(q, top_k=top_k, doc_id=doc_id)


@router.get("/ask")
def rag_ask(
    q: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=20),
    doc_id: Optional[str] = Query(None),
):
    return answer(q, top_k=top_k, doc_id=doc_id)


@router.get("/docs")
def rag_docs():
    return list_docs()
