"""
parser 라우터: Desktop이 PDF를 올리면 페이지 단위 추출 결과를 돌려준다.

  POST /parse/pdf   (multipart/form-data, 필드명 = "file")

결과는 Desktop 화면에 바로 보여주거나 rag/ 인덱싱으로 넘긴다.
로컬 전용(127.0.0.1)이라 인증은 아직 없음 — 배포 시 재검토 (문서 42번).
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from .doc_parser import parse_pdf_bytes
from database.uploads import MAX_PDF, read_upload_capped

router = APIRouter(prefix="/parse", tags=["parser"])

@router.post("/pdf")
async def parse_pdf_endpoint(file: UploadFile = File(...)):
    name = file.filename or "document.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF 파일만 지원합니다.")

    data = await read_upload_capped(file, MAX_PDF)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        doc = parse_pdf_bytes(data, filename=name)
    except Exception as e:  # pdfplumber가 던지는 예외 종류가 다양해 광범위하게 잡는다.
        raise HTTPException(status_code=422, detail=f"PDF 파싱 실패: {e}")

    return doc.to_dict()
