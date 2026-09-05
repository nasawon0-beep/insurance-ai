"""
파이프라인: ParsedDoc를 색인하고, 질문으로 관련 조각을 찾는다 (작업 E, 1~2단계).

여기까지가 "검색". 찾은 조각을 AI에게 넘겨 답을 만드는 3단계(생성)는 아직 없다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunker import chunk_parsed_doc
from .embedder import get_embedder
from .store import VectorStore


def index_parsed_doc(
    doc: dict,
    db_path: str | Path | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """parser의 /parse/pdf 응답(ParsedDoc) → 조각 색인. 같은 doc_id는 기본 교체."""
    chunks = chunk_parsed_doc(doc)
    embedder = get_embedder()
    store = VectorStore(db_path)
    try:
        if replace:
            store.clear(doc["doc_id"])
        if chunks:
            embeddings = embedder.embed([c.text for c in chunks])
            store.add(chunks, embeddings, embedder.name)
        return {
            "doc_id": doc["doc_id"],
            "filename": doc.get("filename"),
            "chunks_indexed": len(chunks),
            "embed_model": embedder.name,
            "total_chunks_in_store": store.count(),
        }
    finally:
        store.close()


def search(
    query: str,
    top_k: int = 5,
    doc_id: str | None = None,
    doc_ids: list[str] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    embedder = get_embedder()
    q_emb = embedder.embed([query])[0]
    store = VectorStore(db_path)
    try:
        hits = store.search(q_emb, top_k=top_k, doc_id=doc_id, doc_ids=doc_ids)
    finally:
        store.close()
    return {"query": query, "embed_model": embedder.name, "hits": hits}


def list_docs(db_path: str | Path | None = None) -> dict[str, Any]:
    store = VectorStore(db_path)
    try:
        return {"docs": store.docs(), "total_chunks": store.count()}
    finally:
        store.close()
