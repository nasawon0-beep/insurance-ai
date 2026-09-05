"""크로스플랫폼 OCR 폴백 (RapidOCR) 스모크 테스트.

두 OCR 백엔드(Vision / RapidOCR)가 모두 없으면 skip.
"""
import io

import pytest


def _router():
    import importlib
    import sys
    from pathlib import Path

    le = Path(__file__).resolve().parents[1] / "local-engine"
    if str(le) not in sys.path:
        sys.path.insert(0, str(le))
    return importlib.import_module("database.router")


def test_ocr_available_flag():
    r = _router()
    assert r._ocr_available() == (r._vision_ocr_available() or r._rapidocr_available())


def _korean_font(size):
    from PIL import ImageFont

    for p in (
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/malgun.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return None


def test_rapidocr_reads_text_from_image_pdf():
    r = _router()
    if not r._rapidocr_available():
        pytest.skip("RapidOCR 미설치")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    font = _korean_font(48)
    if font is None:
        pytest.skip("한국어 폰트 없음 — 렌더 불가")

    img = Image.new("RGB", (900, 240), "white")
    ImageDraw.Draw(img).text((30, 80), "보험료 107244원", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PDF")  # 이미지만 든 PDF
    pdf_bytes = buf.getvalue()

    # 크로스플랫폼 폴백 경로를 직접 검증 (Vision 유무와 무관하게)
    rapid_text = r._ocr_pdf_rapid(pdf_bytes).replace(" ", "")
    assert isinstance(rapid_text, str) and rapid_text  # 뭔가 읽혔다
    # 한글 인식 모델이 붙어 있으면 한글도 읽혀야 한다
    if r._rapidocr_is_korean():
        assert "보험" in rapid_text or "107244" in rapid_text

    # 공개 진입점도 문자열을 돌려준다
    text = r._ocr_pdf(pdf_bytes, "img.pdf")
    assert isinstance(text, str) and text.strip()
