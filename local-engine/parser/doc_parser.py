"""
doc-parser: PDF에서 페이지 단위로 텍스트/표를 추출한다 (작업 D).

원칙:
- 페이지 번호를 절대 잃지 않는다. RAG 근거 인용({..., "page": N})의 뿌리가 여기다 (문서 9번).
- 여기서는 "추출"만 한다. 청킹/임베딩/검색은 rag/ 담당.
- 실패를 조용히 넘기지 않는다. 어떤 페이지가 비었는지(스캔본 등) 표시한다.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber


@dataclass
class ParsedPage:
    page: int  # 1-indexed. 원본 PDF 페이지 번호 그대로 보존한다.
    text: str
    char_count: int
    tables: list[list[list[str | None]]] = field(default_factory=list)
    empty: bool = False  # 텍스트도 표도 못 뽑은 페이지 (예: 스캔 이미지)


@dataclass
class ParsedDoc:
    doc_id: str  # 파일 내용 sha256. 같은 파일이면 항상 같은 id.
    filename: str
    page_count: int
    metadata: dict[str, Any]  # PDF 자체 메타데이터 (제목/작성자 등, 있을 때만)
    pages: list[ParsedPage]
    extracted_at: str  # ISO8601 UTC

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(text: str | None) -> str:
    """페이지 내 잔여 공백만 정리한다. 줄바꿈(레이아웃 힌트)은 보존."""
    if not text:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines).strip()


def parse_pdf_bytes(data: bytes, filename: str = "document.pdf") -> ParsedDoc:
    """PDF 바이트 → ParsedDoc. 업로드 스트림을 그대로 넘길 때 사용."""
    pages: list[ParsedPage] = []
    pdf_meta: dict[str, Any] = {}

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        raw_meta = pdf.metadata or {}
        # PDF 메타데이터 값은 bytes/기타 타입일 수 있어 문자열로 정규화한다.
        pdf_meta = {k: str(v) for k, v in raw_meta.items() if v is not None}

        for i, page in enumerate(pdf.pages, start=1):
            text = _clean(page.extract_text())
            tables = page.extract_tables() or []
            pages.append(
                ParsedPage(
                    page=i,
                    text=text,
                    char_count=len(text),
                    tables=tables,
                    empty=(not text and not tables),
                )
            )

    return ParsedDoc(
        doc_id=_sha256(data),
        filename=filename,
        page_count=len(pages),
        metadata=pdf_meta,
        pages=pages,
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )


def parse_pdf(path: str | Path) -> ParsedDoc:
    """디스크의 PDF 파일 경로 → ParsedDoc."""
    p = Path(path)
    return parse_pdf_bytes(p.read_bytes(), filename=p.name)
