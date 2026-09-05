"""
청킹: ParsedDoc(페이지별 텍스트)를 검색 단위 조각으로 자른다 (작업 E, 1단계).

원칙:
- 페이지 번호를 조각마다 그대로 달고 다닌다. 근거 인용({..., "page": N})의 뿌리 (문서 9번).
- 문단(줄바꿈) 경계를 우선 존중하고, 한 문단이 너무 길 때만 문자 단위로 자른다.
- 표는 텍스트로 펼쳐서 같은 페이지의 조각으로 넣는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_CHUNK_CHARS = 500
DEFAULT_OVERLAP_CHARS = 80


@dataclass
class Chunk:
    chunk_id: str  # "{doc_id}:{page}:{idx}"
    doc_id: str
    filename: str
    page: int  # 원본 PDF 페이지 번호
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split_page_text(text: str, chunk_chars: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 1 > chunk_chars:
            chunks.append(buf)
            buf = ""
        if len(p) <= chunk_chars:
            buf = f"{buf}\n{p}" if buf else p
        else:
            # 단일 문단이 한 조각보다 길다 → 문자 슬라이스 + 오버랩
            if buf:
                chunks.append(buf)
                buf = ""
            start = 0
            step = max(1, chunk_chars - overlap)
            while start < len(p):
                chunks.append(p[start : start + chunk_chars])
                start += step
    if buf:
        chunks.append(buf)
    return chunks


def _render_tables(tables: list | None) -> str:
    blocks: list[str] = []
    for t in tables or []:
        rows = []
        for row in t:
            cells = [("" if c is None else str(c)).strip() for c in row]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            blocks.append("[표]\n" + "\n".join(rows))
    return "\n\n".join(blocks)


def chunk_parsed_doc(
    doc: dict,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """parser의 to_dict() 결과(ParsedDoc) → Chunk 리스트."""
    doc_id = doc["doc_id"]
    filename = doc.get("filename", "document.pdf")

    out: list[Chunk] = []
    for page in doc["pages"]:
        page_no = page["page"]
        parts = _split_page_text(page.get("text", ""), chunk_chars, overlap)
        table_text = _render_tables(page.get("tables"))
        if table_text:
            parts.append(table_text)
        for idx, part in enumerate(parts):
            out.append(
                Chunk(
                    chunk_id=f"{doc_id}:{page_no}:{idx}",
                    doc_id=doc_id,
                    filename=filename,
                    page=page_no,
                    text=part,
                )
            )
    return out
