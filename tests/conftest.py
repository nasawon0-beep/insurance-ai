"""
공통 테스트 픽스처.

- local-engine 을 import 경로에 올린다 (tests/ 는 repo 루트에 있으므로).
- 외부 라이브러리 없이 최소 PDF를 만들어 주는 build_pdf 헬퍼를 제공한다.
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENGINE = REPO_ROOT / "local-engine"
if str(LOCAL_ENGINE) not in sys.path:
    sys.path.insert(0, str(LOCAL_ENGINE))

@pytest.fixture(autouse=True)
def _engine_secret_header(monkeypatch):
    """모든 엔진 TestClient가 실제 앱과 같은 공유 시크릿을 보낸다."""
    original_init = TestClient.__init__

    def authenticated_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        secret = getattr(self.app.state, "api_secret", None)
        header = getattr(self.app.state, "api_secret_header", None)
        if secret and header:
            self.headers[header] = secret

    monkeypatch.setattr(TestClient, "__init__", authenticated_init)


def _build_pdf(pages_text: list[str]) -> bytes:
    """각 페이지에 한 줄 텍스트가 든 유효한 PDF 바이트를 만든다 (라이브러리 불필요).

    xref 오프셋을 실제로 계산해 넣으므로 pdfminer/pdfplumber가 그대로 읽는다.
    """
    n = len(pages_text)
    font_num = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))

    objects: list[tuple[int, str]] = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>"),
    ]
    for i, text in enumerate(pages_text):
        page_num = 3 + 2 * i
        content_num = page_num + 1
        stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET"
        objects.append((
            page_num,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_num} 0 R "
            f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>",
        ))
        objects.append((
            content_num,
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        ))
    objects.append((font_num, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    out = b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for num, body in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1")

    xref_pos = len(out)
    max_num = max(offsets)
    out += f"xref\n0 {max_num + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, max_num + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {max_num + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return out


@pytest.fixture
def build_pdf():
    return _build_pdf
