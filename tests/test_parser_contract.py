"""
작업 D 계약 테스트: PDF 1개 업로드 → 텍스트 추출 → 페이지 보존.

핵심 불변식:
- page_count 가 실제 페이지 수와 같다.
- 각 페이지에 1-indexed `page` 번호가 순서대로 보존된다.
- 각 페이지 텍스트가 그 페이지의 내용과 매칭된다 (섞이지 않는다).
- 같은 파일이면 doc_id(sha256)가 항상 같다.
"""
import pytest

from parser.doc_parser import parse_pdf, parse_pdf_bytes


PAGES = [
    "Alpha clause about hospitalization benefits",
    "Bravo clause about surgery benefits",
    "Charlie clause about exclusions",
]


def test_page_numbers_preserved(build_pdf):
    doc = parse_pdf_bytes(build_pdf(PAGES), filename="policy.pdf")

    assert doc.page_count == 3
    assert [p.page for p in doc.pages] == [1, 2, 3]
    assert "Alpha" in doc.pages[0].text
    assert "Bravo" in doc.pages[1].text
    assert "Charlie" in doc.pages[2].text
    # 페이지가 섞이지 않았는지 (1페이지에 2페이지 내용이 없어야)
    assert "Bravo" not in doc.pages[0].text


def test_doc_id_is_stable(build_pdf):
    data = build_pdf(PAGES)
    a = parse_pdf_bytes(data, filename="a.pdf")
    b = parse_pdf_bytes(data, filename="b.pdf")  # 파일명 달라도 내용 같으면 같은 id
    assert a.doc_id == b.doc_id
    assert len(a.doc_id) == 64


def test_parse_pdf_from_path(tmp_path, build_pdf):
    f = tmp_path / "sample.pdf"
    f.write_bytes(build_pdf(PAGES))
    doc = parse_pdf(f)
    assert doc.filename == "sample.pdf"
    assert doc.page_count == 3


def test_to_dict_shape(build_pdf):
    doc = parse_pdf_bytes(build_pdf(PAGES[:1]), filename="one.pdf")
    d = doc.to_dict()
    assert set(d) >= {"doc_id", "filename", "page_count", "metadata", "pages", "extracted_at"}
    page = d["pages"][0]
    assert set(page) >= {"page", "text", "char_count", "tables", "empty"}
    assert page["page"] == 1


def test_endpoint_contract(build_pdf):
    """POST /parse/pdf 응답이 페이지 번호를 담은 계약 형태인지."""
    httpx = pytest.importorskip("httpx")  # noqa: F841
    from fastapi.testclient import TestClient

    import main  # local-engine/main.py

    client = TestClient(main.app)
    resp = client.post(
        "/parse/pdf",
        files={"file": ("policy.pdf", build_pdf(PAGES), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page_count"] == 3
    assert [p["page"] for p in body["pages"]] == [1, 2, 3]
    assert "Alpha" in body["pages"][0]["text"]


def test_endpoint_rejects_non_pdf():
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    resp = client.post(
        "/parse/pdf",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 415
