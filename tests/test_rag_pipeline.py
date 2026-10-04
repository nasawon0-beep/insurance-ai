"""
작업 E (1~2단계) 계약 테스트: 청킹 + 색인 + 검색.

핵심 불변식:
- 조각(chunk)마다 원본 페이지 번호가 보존된다.
- 질문을 넣으면 관련 조각이 상위로 올라오고, 그 조각의 page 가 맞다.
- AI 답변 생성은 아직 없다 — 검색 결과(근거 조각 + 페이지)까지만.

결정성을 위해 임베더는 HashingEmbedder 로 고정한다 (Ollama 불필요).
"""
import pytest

from parser.doc_parser import parse_pdf_bytes
from rag import pipeline
from rag.chunker import chunk_parsed_doc
from rag.embedder import HashingEmbedder

PAGES = [
    "General provisions. This policy is a contract between the company and the insured person.",
    "Hospitalization benefit. The company pays a daily amount for every night the insured stays in hospital due to illness or injury.",
    "Surgery benefit. The company pays a fixed lump sum when the insured undergoes a covered surgical operation.",
    "Exclusions. The company does not pay for cosmetic procedures, self-inflicted injury, or war.",
]


@pytest.fixture(autouse=True)
def _fixed_embedder(monkeypatch):
    monkeypatch.setattr(pipeline, "get_embedder", lambda: HashingEmbedder())


@pytest.fixture
def parsed(build_pdf):
    return parse_pdf_bytes(build_pdf(PAGES), filename="policy.pdf").to_dict()


def test_chunking_preserves_page_numbers(parsed):
    chunks = chunk_parsed_doc(parsed, chunk_chars=200, overlap=20)
    assert chunks
    assert {c.page for c in chunks} == {1, 2, 3, 4}
    for c in chunks:
        assert c.chunk_id.startswith(parsed["doc_id"])
        assert c.filename == "policy.pdf"


def test_index_then_search_finds_right_page(parsed, tmp_path):
    db = tmp_path / "idx.sqlite3"

    info = pipeline.index_parsed_doc(parsed, db_path=db)
    assert info["chunks_indexed"] >= 4
    assert info["embed_model"] == "hashing-fallback"
    assert info["total_chunks_in_store"] == info["chunks_indexed"]

    res = pipeline.search("surgical operation lump sum", top_k=3, db_path=db)
    assert res["hits"], "검색 결과가 비었음"
    top = res["hits"][0]
    assert top["page"] == 3  # Surgery benefit 는 3페이지
    assert top["score"] > 0
    assert "surg" in top["text"].lower()


def test_reindex_replaces_same_doc(parsed, tmp_path):
    db = tmp_path / "idx.sqlite3"
    first = pipeline.index_parsed_doc(parsed, db_path=db)
    info = pipeline.index_parsed_doc(parsed, db_path=db)  # 두 번째
    # 같은 파일 해시면 재임베딩하지 않고 기존 벡터를 재사용한다.
    assert first["chunks_indexed"] >= 4
    assert info["skipped"] is True
    assert info["reason"] == "same_file_hash"
    assert info["chunks_indexed"] == 0
    assert info["chunks_cached"] == first["chunks_indexed"]
    assert info["total_chunks_in_store"] == first["chunks_indexed"]


def test_docs_metadata_tracks_file_hash(parsed, tmp_path):
    db = tmp_path / "idx.sqlite3"
    info = pipeline.index_parsed_doc(parsed, db_path=db)

    docs = pipeline.list_docs(db_path=db)["docs"]
    doc = next(d for d in docs if d["doc_id"] == parsed["doc_id"])
    assert info["file_hash"] == parsed["doc_id"]
    assert doc["file_hash"] == parsed["doc_id"]
    assert doc["indexed_at"]
    assert doc["chunks"] == info["chunks_indexed"]


def test_search_can_scope_to_doc(parsed, tmp_path):
    db = tmp_path / "idx.sqlite3"
    pipeline.index_parsed_doc(parsed, db_path=db)
    res = pipeline.search("hospital", top_k=5, doc_id=parsed["doc_id"], db_path=db)
    assert all(h["doc_id"] == parsed["doc_id"] for h in res["hits"])
    res_missing = pipeline.search("hospital", top_k=5, doc_id="nope", db_path=db)
    assert res_missing["hits"] == []


def test_endpoints_index_and_search(build_pdf, tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    monkeypatch.setenv("RAG_DB_PATH", str(tmp_path / "http.sqlite3"))
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)

    parsed = client.post(
        "/parse/pdf",
        files={"file": ("policy.pdf", build_pdf(PAGES), "application/pdf")},
    ).json()

    idx = client.post("/rag/index", json=parsed)
    assert idx.status_code == 200
    assert idx.json()["chunks_indexed"] >= 4

    hit = client.get("/rag/search", params={"q": "surgical operation", "top_k": 3})
    assert hit.status_code == 200
    body = hit.json()
    assert body["hits"][0]["page"] == 3

    docs = client.get("/rag/docs").json()
    assert docs["total_chunks"] >= 4
    assert any(d["filename"] == "policy.pdf" for d in docs["docs"])
