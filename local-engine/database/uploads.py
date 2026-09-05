"""업로드 파일 크기 상한과 제한된 읽기 헬퍼."""
import os

from fastapi import HTTPException, UploadFile


_MB = 1024 * 1024

# 값은 양의 정수 MB. import 시점에 1회 읽으므로 변경하려면 엔진 재기동 필요.
MAX_AUDIO = int(os.environ.get("UPLOAD_MAX_AUDIO_MB", "200")) * _MB
MAX_PDF = int(os.environ.get("UPLOAD_MAX_PDF_MB", "100")) * _MB
MAX_SHEET = int(os.environ.get("UPLOAD_MAX_SHEET_MB", "20")) * _MB
MAX_TEXT = int(os.environ.get("UPLOAD_MAX_TEXT_MB", "10")) * _MB


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    detail = f"파일이 너무 큽니다. 최대 {max_bytes // _MB}MB."
    content_length = file.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail=detail)
        except ValueError:
            pass

    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=detail)
    return data
