"""
고객 관리 라우터 (사이드바 '고객').

  POST   /customers                고객 등록
  GET    /customers?q=&limit=&offset=   목록 / 이름·전화 검색
  GET    /customers/{cid}          고객 1명 + 보험계약 목록
  PATCH  /customers/{cid}          고객 수정
  DELETE /customers/{cid}          고객 삭제 (보험계약 CASCADE)

  POST   /customers/{cid}/policies       보험계약 추가
  GET    /customers/{cid}/policies       고객의 보험계약 목록
  PATCH  /policies/{pid}                 보험계약 수정
  DELETE /policies/{pid}                 보험계약 삭제

  POST   /customers/intake/parse        붙여넣은 텍스트 → 고객 필드 추출 (저장 안 함)
  POST   /customers/{cid}/consultations 상담 이력 추가
  GET    /customers/{cid}/consultations 고객의 상담 이력
  POST   /customers/{cid}/consultations/from-audio  녹취 파일 → 전사 + 요약 → 상담 이력
  PATCH  /consultations/{kid}           상담 이력 수정
  DELETE /consultations/{kid}           상담 이력 삭제
  GET    /follow-ups?until=YYYY-MM-DD   후속 연락 예정 목록 (고객명 포함)

로컬 전용(127.0.0.1). 민감 필드는 AES-256-GCM 로 암호화되어 저장된다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from parser.doc_parser import parse_pdf_bytes
from rag import answer as rag_answer
from rag import index_parsed_doc

from . import backup as _backup
from . import coverage, policy_qa, repo
from . import export_data as _export
from . import import_data as _import
from .import_data import ai_classifier as _ai_classifier
from . import settings as _settings
from . import statistics as _stats
from . import usage as _usage
from .db import connect, init_schema
from .intake import extract_customer_fields
from .models import (
    AssistantAsk,
    ConsultationIn,
    ConsultationPatch,
    CustomerIn,
    CustomerPatch,
    IntakeIn,
    PolicyIn,
    PolicyPatch,
)
from .uploads import MAX_AUDIO, MAX_PDF, MAX_SHEET, MAX_TEXT, read_upload_capped

router = APIRouter(tags=["customers"])
logger = logging.getLogger(__name__)

_IMPORT_BATCH_RE = re.compile(r"^[0-9a-f]{32}$")


def _import_batch_id(request: Request) -> Optional[str]:
    value = request.headers.get("X-Import-Batch")
    return value if value and _IMPORT_BATCH_RE.fullmatch(value) else None


def _actor(request: Request) -> str:
    return request.headers.get("X-Actor-Id") or "unknown"


def _audit(conn, request, **kw):
    try:
        repo.log_audit(conn, actor=_actor(request), **kw)
    except Exception:
        logger.warning("audit log write failed", exc_info=True)


def get_conn():
    conn = connect()
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


# ---------- RRN 파일럿 토글 하드 가드 ----------

def _rrn_on(conn) -> bool:
    return _settings.rrn_enabled(conn)


def _scrub_customer(c, enabled: bool):
    """토글 OFF 면 응답에서 주민번호 전체값/마스킹값을 뺀다 (has_rrn 은 유지)."""
    if c and not enabled:
        c["rrn"] = None
        c["rrn_masked"] = None
    return c


def _scrub_intake(item, enabled: bool):
    """토글 OFF 면 추출결과의 fields.rrn 을 null 로, 주민번호 관련 경고를 제거,
    매칭된 기존 고객(item['match'] / candidates)의 rrn·rrn_masked 도 제거."""
    if enabled or not isinstance(item, dict):
        return item
    f = item.get("fields")
    if isinstance(f, dict) and "rrn" in f:
        f["rrn"] = None
    w = item.get("warnings")
    if isinstance(w, list):
        item["warnings"] = [x for x in w if "주민등록번호" not in str(x) and "주민번호" not in str(x)]
    if isinstance(item.get("match"), dict):
        _scrub_customer(item["match"], enabled)
    for cand in item.get("candidates") or []:
        _scrub_customer(cand, enabled)
    return item


def _val_detail(e: ValidationError):
    return [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in e.errors()
    ]


def _import_format(name):
    lower = (name or "").lower()
    if lower.endswith(".xlsx"):
        return "xlsx"
    if lower.endswith(".csv"):
        return "csv"
    raise HTTPException(status_code=415, detail="지원하지 않는 형식입니다. .xlsx 또는 .csv 파일을 선택해주세요.")


@router.post("/customers/intake/parse")
def customers_intake_parse(body: IntakeIn, conn=Depends(get_conn)):
    """붙여넣은 자유 텍스트에서 고객 필드를 뽑아 준다. 저장은 안 함 (미리보기용)."""
    try:
        res = extract_customer_fields(body.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"텍스트 분석 실패: {e}")
    return _scrub_intake(res, _rrn_on(conn))


@router.post("/customers/intake/bulk")
def customers_intake_bulk(body: IntakeIn, conn=Depends(get_conn)):
    """여러 명 붙여넣기 → 각 고객 필드 추출 + 중복 여부. 저장은 안 함 (미리보기)."""
    from . import intake as _intake

    try:
        items = _intake.extract_multiple(body.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"텍스트 분석 실패: {e}")

    enabled = _rrn_on(conn)
    out = []
    for it in items:
        f = it["fields"]
        mr = repo.match_customer(conn, f.get("phone"), f.get("name"), f.get("birth_date"))
        out.append(_scrub_intake({**it, "match": mr["match"], "match_reason": mr["match_reason"]}, enabled))
    return {"items": out}


@router.post("/assistant/ask")
def assistant_ask(body: AssistantAsk, conn=Depends(get_conn)):
    """AI 문의 탭: 자연어로 고객 데이터 질의응답. 대화 기록은 프론트가 재전송(서버 무상태).
    Ollama 미연결 시 502."""
    import socket
    import urllib.error

    from . import assistant as _assistant

    try:
        return _assistant.answer(
            conn,
            body.question,
            [t.model_dump() for t in (body.history or [])],
        )
    except (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout):
        raise HTTPException(
            status_code=502,
            detail="AI 엔진(Ollama)에 연결할 수 없거나 응답이 지연됩니다.",
        )


_CALL_FN_RE = re.compile(r"(?<!\d)(01[016-9]\d{7,8})(?!\d).*?(?<!\d)(\d{6})[_-](\d{6})(?!\d)")
_PHONE11_RE = re.compile(r"(?<!\d)01[016-9]\d{7,8}(?!\d)")
_IOS_KO_RE = re.compile(
    r"(?<!\d)(\d{4})[.-]\s*(\d{1,2})[.-]\s*(\d{1,2})\.?(?!\d)\s*"
    r"(?:(오전|오후)\s*)?(\d{1,2})(?:시|:)(?:\s*(\d{1,2})(?:분)?)?"
)
_GENERAL_DT_RE = re.compile(
    r"(?<!\d)(?:(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})|"
    r"(\d{4})-(\d{2})-(\d{2})[_ ](\d{2})[:.](\d{2})(?::(\d{2}))?)(?!\d)"
)
_DATE_ONLY_RE = re.compile(r"(?<!\d)(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})(\d{2})(\d{2}))(?!\d)")


def _parse_ios_dt(match: re.Match[str]) -> Optional[str]:
    year, month, day, ampm, hour, minute = match.groups()
    hour_i = int(hour)
    if ampm == "오전" and hour_i == 12:
        hour_i = 0
    elif ampm == "오후" and hour_i < 12:
        hour_i += 12
    try:
        return datetime(int(year), int(month), int(day), hour_i, int(minute or 0)).isoformat()
    except ValueError:
        return None


def _parse_general_dt(match: re.Match[str]) -> Optional[str]:
    parts = match.groups()[:6] if match.group(1) else match.groups()[6:]
    year, month, day, hour, minute, second = parts
    try:
        return datetime(*map(int, (year, month, day, hour, minute, second or "0"))).isoformat()
    except ValueError:
        return None


def _parse_date_only(match: re.Match[str]) -> Optional[str]:
    year, month, day = (
        match.groups()[:3]
        if match.group(1)
        else (f"20{match.group(4)}", match.group(5), match.group(6))
    )
    try:
        return datetime(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _parse_call_filename(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'통화 01083518688#_260827_190045.m4a' → ('010-8351-8688', '2026-08-27T19:00:45')."""
    if not name:
        return None, None
    phone = dt = None
    m = _CALL_FN_RE.search(name)
    if m:
        phone = m.group(1)
        d, hms = m.group(2), m.group(3)
        try:
            dt = datetime(
                2000 + int(d[:2]), int(d[2:4]), int(d[4:6]),
                int(hms[:2]), int(hms[2:4]), int(hms[4:6]),
            ).isoformat()
        except ValueError:
            dt = None

    if phone is None:
        pm = _PHONE11_RE.search(name)
        if pm:
            phone = pm.group(0)

    if dt is None:
        for regex, parse in (
            (_IOS_KO_RE, _parse_ios_dt),
            (_GENERAL_DT_RE, _parse_general_dt),
            (_DATE_ONLY_RE, _parse_date_only),
        ):
            gm = regex.search(name)
            if gm:
                dt = parse(gm)
                break
    if phone and len(phone) == 11:
        phone = f"{phone[:3]}-{phone[3:7]}-{phone[7:]}"
    return phone, dt


# 던져넣기/새 고객에 아무 파일이나 끌어다 넣어도 분석하도록 종류를 판정한다.
_AUDIO_EXT = {
    ".m4a", ".mp3", ".wav", ".aac", ".aiff", ".aif", ".ogg", ".oga", ".flac",
    ".opus", ".wma", ".amr", ".3gp", ".mp4", ".mov", ".m4b", ".webm",
}
_MAX_DOC_CHARS = 8000  # PDF/텍스트에서 뽑아 LLM 으로 넘길 최대 길이 (약관 통째로 넣는 경우 방지)


def _upload_kind(filename: Optional[str]) -> str:
    """확장자로 업로드 종류 판정: 'audio' | 'pdf' | 'text'."""
    ext = os.path.splitext((filename or "").lower())[1]
    if ext in _AUDIO_EXT:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    return "text"


# --- 글자 없는 PDF(스캔본 / 벡터로 그려진 보험사 출력물) OCR 폴백 ---
#   1순위: macOS Vision (ocr/pdf-ocr 바이너리) — 빠르고 정확.
#   2순위: pypdfium2 렌더 → RapidOCR(onnxruntime, 한국어+영어 모델 번들). Windows/Linux 대응.
_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
_OCR_BIN = _RESOURCE_ROOT / "ocr" / "pdf-ocr"
_OCR_MAX_PAGES = int(os.environ.get("PDF_OCR_MAX_PAGES", "6"))  # 계약자(앞) + 가입담보목록(3~4p)


def _vision_ocr_available() -> bool:
    return sys.platform == "darwin" and _OCR_BIN.exists()


from functools import lru_cache as _lru_cache


@_lru_cache(maxsize=1)
def _rapidocr_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401

        return True
    except Exception:
        return False


# 한국어 인식 모델 (RapidOCR 기본 번들은 중국어+영어뿐 → 한글이 한자로 깨짐).
#   korean_PP-OCRv4_rec_mobile.onnx + korean_dict.txt 를 ocr/models/ 에 두면 그걸 쓴다.
#   (없으면 기본 ch/en 모델로 폴백 — 숫자·영문은 나오지만 한글은 부정확)
_OCR_MODEL_DIR = _RESOURCE_ROOT / "ocr" / "models"
_KO_REC_ONNX = _OCR_MODEL_DIR / "korean_PP-OCRv4_rec_mobile.onnx"
_KO_REC_DICT = _OCR_MODEL_DIR / "korean_dict.txt"


@_lru_cache(maxsize=1)
def _rapidocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    if _KO_REC_ONNX.exists() and _KO_REC_DICT.exists():
        return RapidOCR(
            rec_model_path=str(_KO_REC_ONNX),
            rec_keys_path=str(_KO_REC_DICT),
        )
    return RapidOCR()


def _rapidocr_is_korean() -> bool:
    return _KO_REC_ONNX.exists() and _KO_REC_DICT.exists()


def _ocr_available() -> bool:
    return _vision_ocr_available() or _rapidocr_available()


def _ocr_info() -> dict:
    vision = _vision_ocr_available()
    rapid = _rapidocr_available()
    return {
        "available": vision or rapid,
        "backend": "vision" if vision else ("rapidocr" if rapid else "none"),
        "vision": vision,
        "rapidocr": rapid,
        "rapidocr_korean": rapid and _rapidocr_is_korean(),
    }


def _ocr_pdf_vision(data: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        proc = subprocess.run(
            [str(_OCR_BIN), tmp.name, str(_OCR_MAX_PAGES)],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0:
            return ""
        out = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        return "\n\n".join(p.get("text", "") for p in out.get("pages", [])).strip()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _ocr_pdf_rapid(data: bytes) -> str:
    import numpy as np
    import pypdfium2 as pdfium

    engine = _rapidocr_engine()
    pdf = pdfium.PdfDocument(data)
    try:
        pages_out: list[str] = []
        for i in range(min(len(pdf), _OCR_MAX_PAGES)):
            page = pdf[i]
            bitmap = page.render(scale=2.9)  # ≈210 DPI — 한국어 작은 고딕 인식률
            pil = bitmap.to_pil().convert("RGB")
            res, _elapsed = engine(np.array(pil))
            if res:
                pages_out.append("\n".join(line[1] for line in res))
        return "\n\n".join(pages_out).strip()
    finally:
        pdf.close()


def _ocr_pdf(data: bytes, filename: Optional[str] = None) -> str:
    """PDF 바이트 → OCR 텍스트. 미지원·실패 시 빈 문자열. 출력 계약은 예전과 동일(평문).

    Vision 이 있으면 먼저 쓰되, 빈 결과면 RapidOCR 로 한 번 더 시도한다."""
    try:
        if _vision_ocr_available():
            t = _ocr_pdf_vision(data)
            if t.strip():
                return t
        if _rapidocr_available():
            return _ocr_pdf_rapid(data)
        return ""
    except Exception:
        return ""


def _pdf_extract_text(data: bytes, max_pages: int = 16) -> str:
    """PDF 앞쪽 페이지에서 텍스트만 뽑는다 (표 추출 생략 — 무거운 벡터 PDF 대응)."""
    import io as _io

    import pdfplumber

    with pdfplumber.open(_io.BytesIO(data)) as pdf:
        return "\n\n".join(
            (pg.extract_text() or "").strip() for pg in pdf.pages[:max_pages]
        ).strip()


def _document_to_text(data: bytes, filename: Optional[str], kind: str) -> str:
    """PDF/텍스트 파일 바이트 → 평문 (고객정보 추출용). 오디오는 여기 오지 않는다."""
    if os.path.splitext((filename or "").lower())[1] in {".xlsx", ".xls"}:
        raise HTTPException(
            status_code=415,
            detail="엑셀 파일(.xlsx)은 여기서 지원하지 않습니다. 상단 '고객 목록' → '일괄 등록' 메뉴를 이용해 주세요.",
        )
    if kind == "pdf":
        try:
            text_joined = _pdf_extract_text(data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"PDF 파싱 실패: {e}")
        # 글자가 없거나 거의 없으면(벡터로 그려졌거나 스캔본) OCR 로 다시 시도한다.
        if len(text_joined) < 40:
            ocr_text = _ocr_pdf(data, filename)
            if ocr_text.strip():
                return ocr_text
        if not text_joined:
            hint = "" if _ocr_available() else " (이 PC에서는 PDF OCR 를 쓸 수 없습니다.)"
            raise HTTPException(
                status_code=422,
                detail="PDF 에서 글자를 추출할 수 없습니다. 스캔본이거나 이미지·벡터로만 "
                       "이루어진 문서입니다." + hint,
            )
        return text_joined
    # 텍스트 파일
    for enc in ("utf-8", "cp949", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise HTTPException(
        status_code=415,
        detail="지원하지 않는 파일 형식입니다. 녹취(mp3·m4a·wav 등), PDF, 텍스트만 분석할 수 있어요.",
    )


# 제안서/청약서 OCR 는 잡음이 많다. 계약자 식별에 쓸 만한 줄만 추린다 (라벨 줄 + 다음 2줄).
_CUSTOMER_LABELS = (
    "계약자", "피보험자", "주피보험자", "성명", "이름", "생년월일", "주민등록번호", "주민번호",
    "연락처", "전화", "휴대", "핸드폰", "주소", "직업", "이메일", "email", "성별", "고객",
)


def _focus_customer_lines(text: str, head: int = 12, cap: int = 1800) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    kept: set[int] = set()
    out: list[str] = []
    for i, ln in enumerate(lines):
        take = i < head and bool(ln)
        if not take and ln and any(k in ln for k in _CUSTOMER_LABELS):
            take = True
        if take:
            for j in (i, i + 1, i + 2):
                if 0 <= j < len(lines) and lines[j] and j not in kept:
                    kept.add(j)
                    out.append(lines[j])
    focused = "\n".join(out)[:cap]
    return focused or text[:cap]


def _strip_fabricated(fields: dict, source_text: str) -> dict:
    """문서에 실제로 없는 주민번호·생년월일·전화번호를 모델이 지어냈으면 지운다."""
    digits = re.sub(r"\D", "", source_text)
    rrn = re.sub(r"\D", "", str(fields.get("rrn") or ""))
    if rrn and rrn[:7] not in digits:  # 앞 7자리(생년월일+성별자리)가 본문에 없음 → 창작
        fields["rrn"] = None
        rrn = ""
    bd = re.sub(r"\D", "", str(fields.get("birth_date") or ""))
    if bd and not rrn and bd not in digits and bd[2:] not in digits:
        fields["birth_date"] = None
    ph = re.sub(r"\D", "", str(fields.get("phone") or ""))
    if ph and ph not in digits:
        fields["phone"] = None
    if not fields.get("rrn"):  # 성별은 rrn 에서 유도됐을 수 있으니 본문 표기로 보정
        if "(여" in source_text or "여자" in source_text:
            fields["gender"] = "F"
        elif "(남" in source_text or "남자" in source_text:
            fields["gender"] = "M"
    return fields


# "(여 31세)" 처럼 성별 표기 옆의 나이를 우선. 없으면 일반 "31세"(단 "90세만기" 류 제외).
_AGE_NEAR_GENDER_RE = re.compile(r"[(（]?\s*[남여]\s*[,)）]?\s*(?:만\s*)?(\d{2})\s*세")
_AGE_ANY_RE = re.compile(r"(?<![0-9])(?:만\s*)?(\d{2})\s*세(?!\s*만기|\s*만납|만기)")
_SANGRYEONG_RE = re.compile(r"보험\s*나이\s*변경일(?:자)?[^0-9]{0,10}(?:매년\s*)?(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _age_in_text(t: str) -> Optional[int]:
    # 1순위: 성별 표기 옆 나이 (가장 확실)
    for mm in _AGE_NEAR_GENDER_RE.finditer(t):
        a = int(mm.group(1))
        if 15 <= a <= 99:
            return a
    # 2순위: 일반 "NN세" — 단 앞 8자에 '기간/만기/납/보험' 이 있으면 "80세만기" 류이므로 무시
    for mm in _AGE_ANY_RE.finditer(t):
        a = int(mm.group(1))
        ctx = t[max(0, mm.start() - 8):mm.start()]
        if 15 <= a <= 99 and not any(w in ctx for w in ("기간", "만기", "납", "보험")):
            return a
    return None


def _estimate_birthdate_from_age(fields: dict, doc_text: str, issued_date: Optional[str]) -> Optional[str]:
    """문서에 생년월일은 없지만 '31세' 같은 나이가 있으면 연도를 추정한다.
    '보험나이 변경일자'(보험상령일)가 있으면 그 6개월 전이 생일 → 월·일까지 추정.
    반환: 경고 문구(추정했을 때) 또는 None."""
    if fields.get("birth_date") or fields.get("rrn"):
        return None
    age = _age_in_text(doc_text)
    if age is None:
        return None

    if issued_date and re.match(r"\d{4}-\d{2}-\d{2}", issued_date):
        ref_y, ref_m, ref_d = (int(x) for x in issued_date[:10].split("-"))
    else:
        t = date.today()
        ref_y, ref_m, ref_d = t.year, t.month, t.day

    s = _SANGRYEONG_RE.search(doc_text)
    if s:
        sm, sd = int(s.group(2)), int(s.group(3))
        b_m = ((sm - 1 - 6) % 12) + 1          # 보험상령일 6개월 전 = 생일 월
        b_d = min(sd, 28)                      # 말일 안전
        passed = (ref_m, ref_d) >= (b_m, b_d)  # 올해 생일 지났나
        year = ref_y - age - (0 if passed else 1)
        fields["birth_date"] = f"{year:04d}-{b_m:02d}-{b_d:02d}"
        return f"생년월일은 문서의 나이({age}세)와 보험상령일로 추정한 값입니다. 정확한 날짜로 확인하세요."

    fields["birth_date"] = f"{ref_y - age:04d}-01-01"
    return f"생년월일은 문서의 나이({age}세)로 연도만 추정했습니다(월·일은 임의). 정확한 날짜로 확인하세요."


def _slice_section(text: str, starts: tuple, ends: tuple, cap: int = 4000) -> str:
    """text 에서 starts 중 먼저 나오는 지점부터 ends 전까지 잘라낸다."""
    lo = next((text.find(k) for k in starts if text.find(k) >= 0), -1)
    if lo < 0:
        return ""
    tail = text[lo:]
    hi = min((tail.find(k, 150) for k in ends if tail.find(k, 150) >= 0), default=-1)
    return (tail[:hi] if hi > 0 else tail)[:cap].strip()


# 긴 가입제안서는 앞쪽 몇 페이지가 약관 보일러플레이트다 — 실제 요약표(계약자/피보험자/보험료)가
# 시작되는 지점을 찾아 그 뒤 일부만 추출 파이프라인에 넘긴다.
# 강한 앵커: 계약자/피보험자/보험료 요약표 자체를 가리키는 라벨 — 이게 있으면 표가 바로 근처다.
_PROPOSAL_BODY_STRONG_ANCHORS = (
    "합계보험료",
    "실납입보험료",
    "납입주기",
    "구분 가입금액 보험기간 납입기간",
)
# 약한 앵커: "OOO 고객님을 위한 (상품)제안서" — 표지·제목일 뿐 실제 표는 훨씬 뒤(다른 페이지)일 수 있다.
_PROPOSAL_BODY_WEAK_ANCHORS = (
    "고객님을 위한 상품제안서",
    "고객님을 위한 가입제안서",
    "고객님을 위한 상품설명서",
)
# "계약자 손유진 여자 36세" / "피보험자 양영철 님 54세 (남자)" 형태만 앵커로 인정한다.
# 뒤에 나이·성별·괄호가 붙어야 실제 당사자 줄이다 — "계약자 또는 피보험자가 …" 같은
# 약관 상용구("또는" 이 2글자라 [가-힣]{2,4} 에 걸림)를 앵커로 오인하지 않도록.
_PROPOSAL_BODY_LINE_RE = re.compile(
    r"(?:계약자|피보험자)\s+[가-힣]{2,4}\s*(?:님)?\s*(?:\d{1,3}\s*세|남자|여자|\()"
)


def _proposal_body(doc_text: str) -> str:
    """제안서 원문에서 약관 보일러플레이트를 지나 실제 요약표가 시작되는 지점부터 ~9000자를 돌려준다.
    강한 앵커(보험료·납입주기·계약자 줄)를 우선하고, 없을 때만 표지 문구 같은 약한 앵커로 폴백한다
    — 표지 문구는 실제 표(다른 페이지)와 멀리 떨어져 있을 수 있어 그것만 믿으면 표를 놓친다.
    아무 앵커도 없으면 기존 동작(앞 4000자)으로 폴백한다."""
    strong = [i for i in (doc_text.find(a) for a in _PROPOSAL_BODY_STRONG_ANCHORS) if i >= 0]
    m = _PROPOSAL_BODY_LINE_RE.search(doc_text)
    if m:
        strong.append(m.start())
    if strong:
        lo = max(0, min(strong) - 500)  # 라벨 조금 앞부터 (표 헤더가 라벨보다 먼저 나올 수 있음)
        return doc_text[lo:lo + 9000]
    weak = [i for i in (doc_text.find(a) for a in _PROPOSAL_BODY_WEAK_ANCHORS) if i >= 0]
    if weak:
        return doc_text[min(weak):min(weak) + 9000]
    return doc_text[:4000]


# 계약자/피보험자 이름을 규칙 기반으로 뽑는다 ("계약자 손유진 여자 36세 …" / "피보험자 양영철 님 54세 (남자)").
_PARTY_TAIL = r"\s*(?:님)?\s*(?:\d{1,3}\s*세|남자|여자|\()"
_POLICYHOLDER_RE = re.compile(r"계약자\s+([가-힣]{2,4})" + _PARTY_TAIL)
_INSURED_RE = re.compile(r"피보험자\s+([가-힣]{2,4})" + _PARTY_TAIL)
_INSURED_GOGAEK_RE = re.compile(r"피보험자\s+([가-힣]{2,4})고객님")
_ADDRESSEE_RE = re.compile(r"([가-힣]{2,4})\s*고객님을\s*위한\s*(?:상품|가입)?\s*제안서")


def _parties_from_text(body: str) -> dict:
    """본문에서 계약자/피보험자 이름을 규칙으로 뽑는다. {policyholder_name, insured_name}."""
    ph = m.group(1) if (m := _POLICYHOLDER_RE.search(body)) else None
    ins = m.group(1) if (m := _INSURED_RE.search(body)) else None
    if not ins and (m := _INSURED_GOGAEK_RE.search(body)):
        ins = m.group(1)
    if not ph and (m := _ADDRESSEE_RE.search(body)):
        ph = m.group(1)
    return {"policyholder_name": ph, "insured_name": ins}


# 보험료 / 납입주기를 규칙 기반으로 뽑는다. ₩/원 값을 $ 값보다 우선한다.
_PREMIUM_RES = (
    re.compile(r"실납입보험료\s*[:\s]*([\d,]+)"),
    re.compile(r"합계\s*보험료\s*[:\s]*(?:[＄$][\d.]+\s*)?\(?\s*[￦₩]?\s*([\d,]+)\s*\)?"),
    re.compile(r"납입\s*보험료\s*[:\s]*[￦₩]?\s*([\d,]+)"),
    re.compile(r"월\s*보험료\s*[:\s]*[￦₩]?\s*([\d,]+)"),
)
_PAYMENT_CYCLE_RE = re.compile(r"납입\s*주기\s*[:\s]*(월납|연납|매월납|1년납|월|년)")
_CYCLE_MONTHLY = {"월납", "월", "매월납"}
_CYCLE_YEARLY = {"연납", "년", "1년납"}


def _premium_from_text(body: str) -> dict:
    """본문에서 보험료/납입주기를 규칙으로 뽑는다. {premium, payment_cycle}."""
    premium = None
    for pat in _PREMIUM_RES:
        for mm in pat.finditer(body):
            digits = mm.group(1).replace(",", "")
            if digits.isdigit() and int(digits) >= 1000:
                premium = int(digits)
                break
        if premium is not None:
            break
    cycle = None
    m = _PAYMENT_CYCLE_RE.search(body)
    if m:
        tok = m.group(1)
        if tok in _CYCLE_MONTHLY:
            cycle = "MONTHLY"
        elif tok in _CYCLE_YEARLY:
            cycle = "YEARLY"
    return {"premium": premium, "payment_cycle": cycle}


_COV_START = ("가입담보목록", "가입담보 목록", "가입담보", "담보명", "보장내용", "담보목록", "보장담보 개요")
_COV_END = ("가입제안서는 약관", "약관을 참고", "상품설명서를", "위 가입담보", "유의사항 안내", "알릴의무사항")
_PLIST_START = ("보유계약리스트", "보유계약 리스트", "보유 계약 리스트",
                "상품별 가입담보상세", "가입담보상세", "가입담보 상세", "정상계약")
_PLIST_END = ("GA2-2지점", "실효/해지", "위 내용은", "컨설턴트", "본 분석", "면책")
_GAP_START = ("보장현황", "보장분석", "보장 현황")
_GAP_END = ("보유계약리스트", "가입담보 상세", "※ 간편", "전체현황")

_CS_SPLIT = re.compile(r"\s*(미가입|부족|충분)\s*(\d{1,3})\s*%\s*")
_CS_AMT = re.compile(r"보장\s*([0-9억,만원.]+)\s*권장금액\s*([0-9억,만원.]+)")


def _parse_coverage_status_pct(doc_text: str) -> list:
    """보장분석서 '보장현황' 그리드(미가입/부족/충분 NN%) → [{name, status, pct, current, recommended}]."""
    seg = _slice_section(doc_text, _GAP_START, _GAP_END, 4000)
    lines = [ln.strip() for ln in seg.splitlines() if ln.strip()]
    out: list = []
    seen: set = set()
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if not _CS_SPLIT.search(a) or not b.startswith("보장"):
            continue
        parts = _CS_SPLIT.split(a)          # [name, st, pct, name, st, pct, ..., tail]
        amts = _CS_AMT.findall(b)
        for k in range((len(parts) - 1) // 3):
            name = parts[k * 3].strip(" ·-\t")
            if not name or len(name) > 22 or name in seen:
                continue
            seen.add(name)
            cur, rec = amts[k] if k < len(amts) else ("", "")
            out.append({
                "name": name,
                "status": parts[k * 3 + 1],
                "pct": int(parts[k * 3 + 2]),
                "current": cur or None,
                "recommended": rec or None,
            })
    return out


# GA "전체 보장현황" 비교표: 담보명 뒤에 '현재 총 보장금액' + 보험사별 컬럼들.
_GA_TOTAL_MARKERS = ("전체 보장현황", "전체보장현황", "기준담보/권장금액", "기준담보")
_GA_ROW_RE = re.compile(r"^([가-힣A-Za-z][가-힣A-Za-z0-9·()%.\-/]*)\s+(.+)$")
_GA_AMT_RE = re.compile(r"^(?:[\d,]+억|[\d,]+만|[\d,]+원|[\d,]+|-|0)$")
_GA_DATE_RE = re.compile(r"\d{4}[-.]\s?\d{1,2}[-.]\s?\d{1,2}|\d{1,2}:\d{2}")
_GA_SKIP_SUBSTR = ("호남GA", "기준담보", "권장금액", "보장현황", "전체현황", "님의", "합계보험료")
_GA_LABEL_ONLY = {
    "사망", "장해", "치매", "암", "암진단", "암 진단", "진단", "뇌", "심장", "뇌/심장",
    "뇌·심장", "수술", "입원", "통원", "운전자", "기타", "실손", "상해", "질병",
    "납입면제", "제자리암", "소액암", "유사암", "후유장해", "간병",
}


def _parse_coverage_status_ga(doc_text: str) -> list:
    """GA '전체 보장현황' 비교표 → [{name, status(가입/미가입), pct=None, current, recommended=None}].
    담보명 다음의 첫 금액 = 현재 총 보장금액. '-'/0 이면 미가입."""
    if not any(m in doc_text for m in _GA_TOTAL_MARKERS):
        return []
    seg = _slice_section(
        doc_text,
        ("전체 보장현황", "전체보장현황", "보장현황"),
        ("상품별 가입담보상세", "가입담보상세", "가입담보 상세", "보유계약리스트"),
        7000,
    ) or doc_text[:7000]
    out: list = []
    seen: set = set()
    for raw in seg.splitlines():
        ln = raw.strip()
        if not ln or _GA_DATE_RE.search(ln) or any(s in ln for s in _GA_SKIP_SUBSTR):
            continue
        m = _GA_ROW_RE.match(ln)
        if not m:
            continue
        name, rest = m.group(1).strip(" ·-\t"), m.group(2).strip()
        if not name or len(name) < 2 or len(name) > 24 or name in seen or name in _GA_LABEL_ONLY:
            continue
        toks = rest.split()
        if not toks or not all(_GA_AMT_RE.match(t) for t in toks):
            continue
        cur = toks[0]
        if cur.endswith("억") and len(toks) > 1 and toks[1].endswith("만"):
            cur = cur + " " + toks[1]
        seen.add(name)
        empty = cur in ("-", "0", "0원")
        out.append({
            "name": name,
            "status": "미가입" if empty else "가입",
            "pct": None,
            "current": None if empty else cur,
            "recommended": None,
        })
    return out


def _parse_coverage_status(doc_text: str) -> list:
    """보장분석서 '보장현황' → [{name, status, pct, current, recommended}].
    두 레이아웃(미가입 NN% / GA 전체 보장현황 비교표)을 모두 지원한다."""
    out = _parse_coverage_status_pct(doc_text)
    have = {o["name"] for o in out}
    for g in _parse_coverage_status_ga(doc_text):
        if g["name"] not in have:
            out.append(g)
    return out

_DOCTYPE_KEYS = (
    ("보장분석", ("보장분석", "보유계약리스트", "간편보장분석", "보장현황", "전체 보장현황")),
    ("가입제안서", ("가입제안서", "가입설계", "가입담보목록", "상품제안서", "상품설명서")),
    ("보험증권", ("보험증권", "증권번호")),
    ("청약서", ("청약서", "청약내용")),
)

# "손유진 고객님을 위한 상품제안서" 처럼 표지 문구로 제안서를 잡는다 (핵심 키워드가 뒤 페이지에 있을 때).
_DOCTYPE_PROPOSAL_RE = re.compile(
    r"[가-힣]{2,4}\s*고객님\s*을?\s*위한[^\n]{0,40}(?:제안서|상품설명서|가입설계|상품요약)"
)


def _doc_type(text: str, filename: Optional[str]) -> str:
    # 표지 앞 몇 페이지가 약관 보일러플레이트인 긴 제안서도 잡을 수 있게 넉넉히 스캔한다.
    hay = (text[:6000] + " " + (filename or ""))
    for label, keys in _DOCTYPE_KEYS:
        if any(k in hay for k in keys):
            return label
    if _DOCTYPE_PROPOSAL_RE.search(hay):
        return "가입제안서"
    return "보험문서"


_FN_DROP = ("보장분석", "가입제안서", "제안서", "청약서", "증권", "분석", "설계", "고객", "님", "요약", "M")
_HANGUL_NAME = re.compile(r"[가-힣]{2,4}")


def _name_from_filename(filename: Optional[str]) -> Optional[str]:
    """'260901_M보장분석_이영희님.pdf' → '이영희'. 문서 내 이름이 마스킹(*)됐을 때 폴백."""
    if not filename:
        return None
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"[0-9_\-]+", " ", stem)
    for tok in _FN_DROP:
        stem = stem.replace(tok, " ")
    cands = [m.group(0) for m in _HANGUL_NAME.finditer(stem)]
    return cands[-1] if cands else None


def _doc_consultation(doc_type: str, policies: list, extra_text, filename: Optional[str], issued: Optional[str]) -> dict:
    """보험 문서 → 상담 이력 1건. 채널 = 문서 종류. extra_text 는 담보 원문(str) 또는 보장현황(list)."""
    lines = [f"[{doc_type} 검토]"]
    if policies:
        total = sum(int(p["premium_won"]) for p in policies if p.get("premium_won"))
        lines.append(f"보유/제안 계약 {len(policies)}건" + (f" · 월 합계 {total:,}원" if total else ""))
        for p in policies[:20]:
            bits = [p.get("insurer"), p.get("product_name")]
            tail = " / ".join(
                x for x in [
                    f"{int(p['premium_won']):,}원" if p.get("premium_won") else None,
                    p.get("insured_period"),
                    p.get("payment_period"),
                ] if x
            )
            lines.append("· " + " ".join(b for b in bits if b) + (f"  ({tail})" if tail else ""))
    if filename:
        lines.append(f"파일: {filename}")
    content = "\n".join(lines)
    if isinstance(extra_text, list) and extra_text:  # 보장현황(구조화)
        mark = {"미가입": "✕", "부족": "▲", "충분": "○", "가입": "○"}
        gl = ["", "[보장현황]"]
        for g in extra_text:
            if g.get("recommended"):
                amt = f"  (현재 {g['current']} / 권장 {g['recommended']})"
            elif g.get("current"):
                amt = f"  (현재 {g['current']})"
            else:
                amt = ""
            pct = f" {g['pct']}%" if g.get("pct") is not None else ""
            gl.append(f"{mark.get(g['status'], '·')} {g['name']}: {g['status']}{pct}{amt}")
        content += "\n".join(gl)
    elif isinstance(extra_text, str) and extra_text:  # 담보 원문
        content += "\n\n[가입담보목록 요약]\n" + extra_text[:1800]
    head = policies[0].get("product_name") or policies[0].get("insurer") if policies else "보험 문서"
    cov_json = (
        json.dumps(extra_text, ensure_ascii=False)
        if isinstance(extra_text, list) and extra_text else None
    )
    return {
        "consulted_at": issued or None,
        "channel": doc_type,
        "title": f"{doc_type} 검토 — {str(head)[:30]}" + (f" 외 {len(policies) - 1}건" if len(policies) > 1 else ""),
        "content": content,
        "transcript": None,
        "follow_up_at": None,
        "coverage_json": cov_json,
    }


def _transcribe_and_summarize(data: bytes, filename: Optional[str]):
    """녹취 바이트 → {stt, summary, fn_phone, fn_dt}. 임시파일 처리 포함."""
    fn_phone, fn_dt = _parse_call_filename(filename)
    suffix = os.path.splitext(filename or "audio.m4a")[1] or ".m4a"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        from whisper import summarize, transcribe
        from whisper.transcriber import ModelNotReady

        stt = transcribe(tmp.name)
        if not stt["text"].strip():
            raise HTTPException(status_code=422, detail="전사 결과가 비어 있습니다.")
        summary = summarize(stt["text"])
    except HTTPException:
        raise
    except ModelNotReady as e:
        return JSONResponse(status_code=409, content={
            "detail": "음성 인식 파일을 준비하고 있습니다. 잠시 후 자동으로 다시 시도합니다.",
            "stt_status": e.args[0],
        })
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"녹취 처리 실패: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return {"stt": stt, "summary": summary, "fn_phone": fn_phone, "fn_dt": fn_dt}


@router.post("/capture")
async def capture(
    file: Optional[UploadFile] = File(None),
    text: str = Form(""),
    context: str = Form(""),
    conn=Depends(get_conn),
):
    """홈 '던져넣기' (통합): 녹취·PDF·텍스트 무엇이든, 한 명이든 여러 명이든 넣으면
    각 고객을 추출하고 전화/이름+생년월일로 기존 고객과 매칭한다. 저장은 안 함 (미리보기).
    응답 items[i] = {fields, warnings, match, match_reason, consultation?}."""
    from . import intake as _intake

    has_file = file is not None and (file.filename or "") != ""
    if not has_file and not text.strip():
        raise HTTPException(status_code=400, detail="파일이나 텍스트 중 하나는 필요합니다.")

    # 업로드 분석 정확도용 참고 정보 (저장 안 함 — 추출 파이프라인 입력에만 덧붙인다).
    ctx_prefix = f"[참고 정보] {context.strip()}\n\n" if context.strip() else ""

    items: list[dict] = []
    audio_minutes = None

    if has_file:
        kind = _upload_kind(file.filename)
        max_bytes = {"audio": MAX_AUDIO, "pdf": MAX_PDF, "text": MAX_TEXT}[kind]
        data = await read_upload_capped(file, max_bytes)
        if not data:
            raise HTTPException(status_code=400, detail="빈 파일입니다.")
        if kind == "audio":
            r = _transcribe_and_summarize(data, file.filename)
            if isinstance(r, Response):
                return r
            stt, s = r["stt"], r["summary"]
            fn_phone, fn_dt = r["fn_phone"], r["fn_dt"]
            hint = f"\n\n(참고 — 파일명 전화번호: {fn_phone})" if fn_phone else ""
            if ctx_prefix:
                hint += f"\n\n{ctx_prefix.strip()}"
            combo = stt["text"] + (("\n\n" + text.strip()) if text.strip() else "") + hint
            ex = extract_customer_fields(combo)
            f = ex["fields"]
            if not f.get("phone") and fn_phone:
                f["phone"] = fn_phone
            warnings = list(ex["warnings"])
            if fn_phone is None and fn_dt is None:
                warnings.append("파일명에서 전화번호·통화일시를 읽지 못했습니다. 상담 저장 시 직접 입력해 주세요.")
            items.append({
                "fields": f,
                "warnings": warnings,
                "consultation": {
                    "consulted_at": fn_dt,
                    "channel": "전화 녹취",
                    "title": s["title"],
                    "content": _summary_to_content(s),
                    "transcript": stt["text"],
                    "follow_up_at": s["follow_up_at"],
                },
            })
            audio_minutes = stt["audio_minutes"]
        else:
            # PDF / 텍스트 파일 → 글자(또는 OCR)를 뽑아 분석.
            doc_text = _document_to_text(data, file.filename, kind)
            if ctx_prefix:
                doc_text = ctx_prefix + doc_text
            if kind == "pdf":
                doc_type = _doc_type(doc_text, file.filename)
                # 긴 제안서는 앞쪽이 약관 보일러플레이트라, 실제 요약표(계약자/피보험자/보험료)가
                # 시작되는 지점부터 잘라 추출 파이프라인에 넘긴다.
                prop_body = _proposal_body(doc_text)
                # 잡음 많은 OCR 에서 계약자 식별에 쓸 줄만 추리고, '모집자(설계사)' 오인·창작 방지.
                # 요약표 슬라이스도 덧붙여 앞 12줄이 약관인 긴 문서에서도 계약자/피보험자가 잡히게 한다.
                focused = (_focus_customer_lines(doc_text) + "\n" + prop_body)[:3000]
                guide = (
                    f"[보험 {doc_type} OCR 발췌]\n"
                    "※ 계약자(피보험자) 본인 정보만 뽑아라. '모집자·보험모집인·설계사·컨설턴트·대리점' 의 "
                    "이름·전화·주소·소속은 넣지 마라. 발췌에 없는 주민등록번호·생년월일·전화번호는 "
                    "절대 지어내지 말고 null 로 둬라.\n\n"
                )
                extra = ("\n\n" + text.strip()) if text.strip() else ""
                ex = extract_customer_fields(guide + focused + extra)
                f = _strip_fabricated(ex["fields"], doc_text + extra)
                warns = list(ex["warnings"])

                # 이름이 마스킹(나*원)됐거나 비었으면 파일명에서 복구
                nm = str(f.get("name") or "")
                if not nm or "*" in nm or "○" in nm or "◯" in nm:
                    fn_nm = _name_from_filename(file.filename)
                    if fn_nm:
                        f["name"] = fn_nm

                # 문서에서 나온 rrn 이 13자리가 아니면 생년월일을 잘못 읽은 것 → 버린다
                if f.get("rrn") and len(re.sub(r"\D", "", str(f["rrn"]))) != 13:
                    f["rrn"] = None
                if not f.get("rrn"):
                    warns = [w for w in warns if "주민등록번호" not in w]

                # 계약: 보장분석/보유계약리스트면 여러 건, 아니면 증권 헤더 1건
                coverage_status: list = []
                if doc_type == "보장분석" or "보유계약리스트" in doc_text:
                    plist = _intake.extract_policies_list(
                        _slice_section(doc_text, _PLIST_START, _PLIST_END, 4000)
                    )
                    policies = plist["policies"]
                    warns += plist["warnings"]
                    coverage_status = _parse_coverage_status(doc_text)
                    consult_extra = coverage_status  # 구조화 리스트
                    cov_raw = _slice_section(doc_text, _GAP_START, _GAP_END, 3500)
                else:
                    # prop_body(7000자) 를 통째로 LLM 에 넣으면 num_ctx(4096) 를 넘어 응답이
                    # 비거나(빈 JSON) 극도로 느려진다(실측 118초). 요약표는 앵커 직후 몇백자
                    # 안에 다 있으므로, LLM 호출은 앞부분만 잘라 보낸다. 정규식 헬퍼들
                    # (_parties_from_text/_premium_from_text) 은 계속 prop_body 전체를 본다.
                    pr = _intake.extract_policy_fields(prop_body[:2200])
                    p1 = pr["policy"]
                    warns += pr["warnings"]
                    cov_raw = _slice_section(doc_text, _COV_START, _COV_END, 3500)
                    consult_extra = cov_raw  # 담보 원문 문자열

                    # 계약자/피보험자 이름은 규칙 기반 추출이 LLM 보다 확실하다 — 찾았으면 덮어쓴다.
                    det_parties = _parties_from_text(prop_body + "\n" + doc_text[:1500])
                    if det_parties.get("insured_name"):
                        p1["insured_name"] = det_parties["insured_name"]
                    if det_parties.get("policyholder_name"):
                        p1["policyholder_name"] = det_parties["policyholder_name"]
                    # 한쪽만 찾았으면 본인계약으로 보고 나머지 쪽도 같게 채운다.
                    if p1.get("insured_name") and not p1.get("policyholder_name"):
                        p1["policyholder_name"] = p1["insured_name"]
                    elif p1.get("policyholder_name") and not p1.get("insured_name"):
                        p1["insured_name"] = p1["policyholder_name"]

                    # 보험료/납입주기: '실납입보험료'/'합계보험료' 처럼 명시적으로 라벨 붙은
                    # 총보험료는 LLM 이 특약별 보험료 등 다른 숫자를 집었더라도 규칙 기반이
                    # 더 정확하다 — 찾았으면 항상 그 값으로 덮어쓴다 (계약자/피보험자와 동일 원칙).
                    det_prem = _premium_from_text(prop_body)
                    if det_prem["premium"] is not None:
                        p1["premium_won"] = det_prem["premium"]
                    if det_prem["payment_cycle"]:
                        p1["payment_cycle"] = det_prem["payment_cycle"]

                    policies = [p1] if (p1.get("insurer") or p1.get("product_name")) else []

                    # 고객명이 비어 있는 긴 문서: 규칙으로 찾은 피보험자(우선)/계약자로 채운다.
                    if not str(f.get("name") or "").strip():
                        fallback_nm = p1.get("insured_name") or p1.get("policyholder_name")
                        if fallback_nm:
                            f["name"] = fallback_nm
                            warns = [w for w in warns if "이름을 찾지 못했습니다" not in w]

                    # 계약자(契約者) ≠ 피보험자(被保險者): 제안서에 피보험자 이름이 따로
                    # 적혀 있고 계약자(또는 추출된 고객명)와 다르면, 피보험자 기준으로 등록(match)하고
                    # 계약자 이름을 계약에 실어 둔다. 관계(policyholder_rel)는 UI 에서 고른다.
                    insured_nm = str(p1.get("insured_name") or "").strip()
                    ph_nm = str(p1.get("policyholder_name") or "").strip()
                    cust_nm = str(f.get("name") or "").strip()
                    differs = (insured_nm and ph_nm and insured_nm != ph_nm) or \
                              (insured_nm and cust_nm and insured_nm != cust_nm)
                    if differs:
                        contractor = ph_nm or cust_nm
                        f["name"] = insured_nm or cust_nm
                        for _p in policies:
                            _p["policyholder_name"] = contractor
                        warns.append(
                            f"계약자({contractor})와 피보험자({insured_nm})가 다릅니다 — "
                            f"피보험자({insured_nm}) 기준으로 등록됩니다. "
                            f"생년월일·연락처가 누구 것인지 저장 전에 확인하세요."
                        )
                    else:
                        # 본인계약(계약자=피보험자) — 계약자 필드는 남기지 않는다.
                        for _p in policies:
                            _p["policyholder_name"] = None
                    for _p in policies:
                        _p.pop("insured_name", None)

                # "내가 가입시킴" 플래그 기본값: 보장분석/보유계약리스트는 남의 계약 목록 → False,
                # 가입제안서·보험증권·청약서·기타는 내가 진행하는 계약 → True.
                own_default = not (doc_type == "보장분석" or "보유계약리스트" in doc_text)
                for _p in policies:
                    _p["is_own"] = own_default

                issued = next((p.get("issued_date") for p in policies if p.get("issued_date")), None)
                est_warn = _estimate_birthdate_from_age(f, doc_text + extra, issued)
                if est_warn:
                    warns.append(est_warn)

                items.append({
                    "fields": f,
                    "warnings": warns,
                    "doc_type": doc_type,
                    "consultation": (
                        _doc_consultation(doc_type, policies, consult_extra, file.filename, issued)
                        if (policies or consult_extra) else None
                    ),
                    "policies": policies,
                    "coverage_status": coverage_status,
                    "coverages_text": cov_raw,
                })
            else:
                combined = doc_text + (("\n\n" + text.strip()) if text.strip() else "")
                combined = combined.strip()[:_MAX_DOC_CHARS]
                if not combined:
                    raise HTTPException(status_code=422, detail="파일에서 분석할 내용을 찾지 못했습니다.")
                try:
                    extracted = _intake.extract_multiple(combined)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"텍스트 분석 실패: {e}")
                for it in extracted:
                    items.append({
                        "fields": it["fields"],
                        "warnings": list(it["warnings"]),
                        "consultation": None,
                    })
    else:
        try:
            extracted = _intake.extract_multiple(ctx_prefix + text)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"텍스트 분석 실패: {e}")
        for it in extracted:
            items.append({
                "fields": it["fields"],
                "warnings": list(it["warnings"]),
                "consultation": None,
            })

    enabled = _rrn_on(conn)
    for it in items:
        f = it["fields"]
        mr = repo.match_customer(conn, f.get("phone"), f.get("name"), f.get("birth_date"))
        it["match"] = mr["match"]
        it["match_reason"] = mr["match_reason"]
        if mr["match_reason"] == "name":
            it["warnings"].append("이름만 일치합니다. 동명이인일 수 있으니 확인하세요.")
        _scrub_intake(it, enabled)

    _cap_source = "text"
    if has_file:
        _cap_source = _upload_kind(file.filename)
        if _cap_source not in ("audio", "pdf"):
            _cap_source = "text"
    try:
        _usage.record(conn, "capture_run", {"source": _cap_source, "items": len(items)})
    except Exception:
        pass

    return {
        "items": items,
        "audio_minutes": audio_minutes,
    }


@router.post("/customers/intake/from-audio")
async def intake_from_audio(file: UploadFile = File(...), conn=Depends(get_conn)):
    """녹취 파일 → 전사 + 요약 + 고객정보 추출. 저장은 안 함 (미리보기).
    확인 후 프론트가 /customers 로 생성하고 consultation 을 붙인다."""
    data = await read_upload_capped(file, MAX_AUDIO)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    fn_phone, fn_dt = _parse_call_filename(file.filename)
    suffix = os.path.splitext(file.filename or "audio.m4a")[1] or ".m4a"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        from whisper import summarize, transcribe

        stt = transcribe(tmp.name)
        if not stt["text"].strip():
            raise HTTPException(status_code=422, detail="전사 결과가 비어 있습니다.")
        summary = summarize(stt["text"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"녹취 처리 실패: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    hint = f"\n\n(참고 — 파일명에서 추출한 전화번호: {fn_phone})" if fn_phone else ""
    extracted = extract_customer_fields(stt["text"] + hint)
    if fn_phone is None and fn_dt is None:
        extracted.setdefault("warnings", []).append(
            "파일명에서 전화번호·통화일시를 읽지 못했습니다. 상담 저장 시 직접 입력해 주세요."
        )
    _scrub_intake(extracted, _rrn_on(conn))
    fields = extracted["fields"]
    if not fields.get("phone") and fn_phone:
        fields["phone"] = fn_phone

    return {
        "fields": fields,
        "warnings": extracted["warnings"],
        "transcript": stt["text"],
        "summary": summary,
        "consultation": {
            "consulted_at": fn_dt,
            "channel": "전화 녹취",
            "title": summary["title"],
            "content": _summary_to_content(summary),
            "transcript": stt["text"],
            "follow_up_at": summary["follow_up_at"],
        },
        "audio_minutes": stt["audio_minutes"],
    }


@router.post("/customers", status_code=201)
async def create_customer(request: Request, conn=Depends(get_conn)):
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="JSON 본문이 필요합니다.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="본문은 객체여야 합니다.")
    enabled = _rrn_on(conn)
    if not enabled:
        raw.pop("rrn", None)  # 토글 OFF: 모델 검증 전에 제거 (422 도 안 나게)
    try:
        body = CustomerIn(**raw)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=_val_detail(e))
    if body.rrn:
        dup = repo.find_customer_id_by_rrn(conn, body.rrn)
        if dup:
            raise HTTPException(
                status_code=409,
                detail=f"같은 주민등록번호의 고객이 이미 있습니다 (id={dup}).",
            )
    try:
        customer = repo.create_customer(conn, body.model_dump())
    except repo.DuplicateRRNError:
        raise HTTPException(
            status_code=409, detail="같은 주민등록번호의 고객이 이미 있습니다"
        )
    bid = _import_batch_id(request)
    if bid:
        repo.stamp_import_batch(conn, "customers", customer["id"], bid)
    response = _scrub_customer(customer, enabled)
    if enabled and response.get("rrn"):
        repo.log_rrn_access(
            conn,
            response["id"],
            "고객 저장 응답에 주민등록번호 포함",
            request.headers.get("X-Actor-Id") or "unknown",
            "create_response",
        )
    _audit(conn, request, action="create", entity="customer", entity_id=customer["id"], customer_id=customer["id"])
    return response


@router.get("/customers")
def list_customers(
    q: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),  # 페이지네이션 전까지 전체 로드 (작업 #5에서 개선)
    offset: int = Query(0, ge=0),
    sort: str = Query("name", pattern="^(name|recent|expiry|follow_up|birthday)$"),
    tag: Optional[str] = Query(None),
    expiring_days: Optional[int] = Query(None, ge=1, le=365),
    has_follow_up: bool = Query(False),
    status: Optional[str] = Query(None, pattern="^(가입|미가입|가망|해지)$"),
    own: Optional[int] = Query(None),  # 1 이면 '내가 가입시킨' 계약이 1건 이상인 고객만
    conn=Depends(get_conn),
):
    enabled = _rrn_on(conn)
    return {
        "customers": [
            _scrub_customer(c, enabled)
            for c in repo.list_customers(
                conn, q=q, limit=limit, offset=offset, sort=sort,
                tag=tag, expiring_days=expiring_days, has_follow_up=has_follow_up,
                status=status, own=own,
            )
        ]
    }


@router.get("/customers/match")
def match_customer(
    phone: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    birth_date: Optional[str] = Query(None),
    conn=Depends(get_conn),
):
    """중복 고객 탐지 (전화 > 이름+생년월일 > 이름). /customers/{cid} 보다 먼저 선언."""
    res = repo.match_customer(conn, phone, name, birth_date)
    enabled = _rrn_on(conn)
    _scrub_customer(res.get("match"), enabled)
    for cand in res.get("candidates") or []:
        _scrub_customer(cand, enabled)
    return res


@router.get("/tags")
def list_tags(conn=Depends(get_conn)):
    return {"tags": repo.all_tags(conn)}


@router.get("/diagnostics")
def diagnostics(conn=Depends(get_conn)):
    """설정·진단 화면 한 번에: 엔진 / 암호화 / Whisper / 데이터 / RAG."""
    import json as _json
    import urllib.request as _u

    engine: dict = {"local_engine": "ok"}
    try:
        with _u.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            engine["ollama"] = "connected"
            engine["models"] = [m["name"] for m in _json.loads(r.read()).get("models", [])]
    except Exception as e:
        engine["ollama"] = "disconnected"
        engine["error"] = str(e)

    from .crypto import get_cipher

    try:
        import whisper
        winfo = whisper.info()
    except Exception as e:
        winfo = {"error": str(e)}

    try:
        from rag import list_docs
        rag_info = list_docs()
    except Exception as e:
        rag_info = {"error": str(e)}

    rrn_enabled, rrn_source = _settings.rrn_setting(conn)
    try:
        backups = _backup.list_backups()
    except Exception:
        backups = []

    return {
        "engine": engine,
        "customer_db": {
            "encryption": "AES-256-GCM (field-level)",
            "key_source": get_cipher().key_source,
        },
        "whisper": winfo,
        "ocr": _ocr_info(),
        "rrn_input": {"enabled": rrn_enabled, "source": rrn_source, "locked": _settings.rrn_locked_by_env()},
        "backups": {"count": len(backups), "latest": backups[0]["created_at"] if backups else None},
        "data": repo.dashboard_counts(conn),
        "rag": rag_info,
        "audit": repo.audit_stats(conn),
    }


# ==================== 파일럿 준비 배치 ====================
# (route 순서: 리터럴 경로는 /customers/{cid} 보다 위. 여기 있으면 안전.)

@router.get("/settings")
def get_settings(conn=Depends(get_conn)):
    enabled, source = _settings.rrn_setting(conn)
    return {"rrn_input_enabled": enabled, "source": source}


@router.get("/capture/batches")
def capture_batches(limit: int = Query(5, ge=1, le=20), conn=Depends(get_conn)):
    now = datetime.now(timezone.utc)
    batches = []
    for item in repo.recent_batches(conn, limit):
        created = datetime.fromisoformat(item["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires = created + timedelta(minutes=30)
        batches.append({
            **item,
            "undoable": now < expires,
            "expires_at": expires.isoformat(),
        })
    return {"batches": batches}


@router.post("/capture/batches/{batch_id}/undo")
def undo_capture_batch(batch_id: str, request: Request, conn=Depends(get_conn)):
    if not _IMPORT_BATCH_RE.fullmatch(batch_id):
        raise HTTPException(status_code=422, detail="올바른 배치 ID가 아닙니다.")
    ids = repo.batch_row_ids(conn, batch_id)
    rrn_targets = repo.rrn_batch_targets(conn, batch_id)
    if not any(ids.values()) and not rrn_targets:
        raise HTTPException(status_code=404, detail="되돌릴 항목이 없습니다.")
    batch = next((b for b in repo.recent_batches(conn, 1_000_000) if b["id"] == batch_id), None)
    if batch:
        created = datetime.fromisoformat(batch["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= created + timedelta(minutes=30):
            raise HTTPException(status_code=410, detail="되돌릴 수 있는 시간이 지났습니다.")

    counts = {kind: 0 for kind in ids}
    for pid in ids["policies"]:
        counts["policies"] += int(repo.delete_policy(conn, pid))
    for kid in ids["consultations"]:
        counts["consultations"] += int(repo.delete_consultation(conn, kid))
    n_rrn = 0
    for cid, rrn_hash in rrn_targets:
        n_rrn += repo.clear_customer_rrn(conn, cid, rrn_hash)
    conn.execute("DELETE FROM import_batch_rrn WHERE batch_id = ?", (batch_id,))
    for cid in ids["customers"]:
        counts["customers"] += int(repo.delete_customer(conn, cid))
    conn.commit()
    if not any(counts.values()) and n_rrn == 0:
        raise HTTPException(status_code=404, detail="되돌릴 항목이 없습니다.")
    _usage.record(conn, "capture_undone", counts)
    _audit(conn, request, action="delete", entity="import", entity_id=batch_id, fields=f"{counts['customers']} customers, {counts['policies']} policies, {counts['consultations']} consultations, {n_rrn} rrn")
    logger.warning(
        "capture batch undo: batch=%s actor=%s deleted=%s",
        batch_id,
        request.headers.get("X-Actor-Id") or "unknown",
        counts,
    )
    return {"deleted": counts, "rrn_cleared": n_rrn}


@router.patch("/settings")
async def patch_settings(request: Request, conn=Depends(get_conn)):
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="JSON 본문이 필요합니다.")
    if not isinstance(raw, dict) or "rrn_input_enabled" not in raw:
        raise HTTPException(status_code=422, detail="rrn_input_enabled (bool) 이 필요합니다.")
    if _settings.rrn_locked_by_env():
        raise HTTPException(
            status_code=403,
            detail={
                "message": (
                    "주민등록번호 입력 기능이 환경설정(RRN_INPUT_ENABLED)으로 고정되어 있어 "
                    "앱에서 변경할 수 없습니다. 개인정보보호법상 주민등록번호는 법령에 근거가 "
                    "있을 때만 수집·보관할 수 있어, 배포 시 관리자가 정책으로 결정합니다."
                ),
                "locked_by": "env",
            },
        )
    _settings.set_rrn_enabled(conn, bool(raw["rrn_input_enabled"]))
    enabled, source = _settings.rrn_setting(conn)
    return {"rrn_input_enabled": enabled, "source": source}


@router.post("/maintenance/backup")
def maintenance_backup(conn=Depends(get_conn)):
    try:
        r = _backup.make_backup("manual")
    except Exception as e:  # 디스크 풀 등 — 500 대신 사유를 담아 200
        return {"created": False, "reason": str(e), "pruned": 0, "trigger": "manual"}
    try:
        _usage.record(conn, "backup_made", {"trigger": "manual", "pruned": r.get("pruned", 0)})
    except Exception:
        pass
    return r


@router.get("/backups")
def get_backups():
    return {"backups": _backup.list_backups(), "keep": _backup.KEEP_N}


@router.post("/backups/restore")
async def post_backup_restore(request: Request, conn=Depends(get_conn)):
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="JSON 본문이 필요합니다.")
    fn = raw.get("filename") if isinstance(raw, dict) else None
    if not fn:
        raise HTTPException(status_code=422, detail="filename 이 필요합니다.")
    try:
        r = _backup.restore_backup(fn)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        _usage.record(conn, "restore_run", {})
    except Exception:
        pass
    return r


@router.get("/export")
def get_export(request: Request, conn=Depends(get_conn)):
    actor = request.headers.get("X-Actor-Id") or "unknown"

    def audit_rrn_export(customer_ids: list[str]) -> None:
        repo.log_rrn_access_many(
            conn,
            customer_ids,
            "데이터 내보내기(고객.csv)",
            actor,
            "export",
        )

    r = _export.build_export(conn, rrn_audit=audit_rrn_export)
    try:
        _usage.record(conn, "export_run", {
            "format": "zip", "rows": r["rows"], "include_rrn": r["include_rrn"],
        })
    except Exception:
        pass
    return r


@router.get("/exports")
def get_exports():
    return {"exports": _export.list_exports()}


@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...)):
    file_format = _import_format(file.filename)
    data = await read_upload_capped(file, MAX_SHEET)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    try:
        preview = _import.preview(data, file_format=file_format)
    except _import._InvalidImport as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _import._XlsxReadError:
        raise HTTPException(status_code=400, detail="엑셀 파일을 읽을 수 없습니다(손상·암호화·형식 아님).")
    try:
        preview["auto_mapping"] = await _ai_classifier.auto_map_columns(preview["columns"])
        sample_rows = [
            [row.get(col) for col in preview["columns"]]
            for row in preview["sample_rows"]
        ]
        preview["ai_sample_rows"] = await _ai_classifier.classify_and_extract_batch(sample_rows)
    except Exception as e:  # noqa: BLE001 — AI 보조 경로 실패가 기존 import preview를 막으면 안 된다.
        preview["auto_mapping"] = {}
        preview["ai_sample_rows"] = []
        preview["ai_error"] = str(e)
    return preview


@router.post("/import/commit")
async def import_commit(
    request: Request,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    dedupe: str = Form("merge"),
    conn=Depends(get_conn),
):
    file_format = _import_format(file.filename)
    data = await read_upload_capped(file, MAX_SHEET)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    try:
        mp = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="mapping 은 JSON 이어야 합니다.")
    if not isinstance(mp, dict):
        raise HTTPException(status_code=422, detail="mapping 은 객체여야 합니다.")
    try:
        r = _import.commit(conn, data, mp, dedupe, _rrn_on(conn), file_format=file_format)
    except _import._NameUnmapped:
        raise HTTPException(status_code=422, detail="이름(name) 컬럼 매핑이 필요합니다.")
    except _import._InvalidImport as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _import._XlsxReadError:
        raise HTTPException(status_code=400, detail="엑셀 파일을 읽을 수 없습니다(손상·암호화·형식 아님).")
    try:
        _usage.record(conn, "import_run", {
            "created": r["created"], "merged": r["merged"], "failed": len(r["failed"]),
        })
    except Exception:
        pass
    _audit(conn, request, action="create", entity="import", entity_id=repo._new_id(), fields=f"{r['created']} created, {r['merged']} merged")
    return r


@router.get("/audit")
def list_audit(limit: int = Query(200, ge=1, le=1000), customer_id: Optional[str] = Query(None), conn=Depends(get_conn)):
    return repo.list_audit(conn, customer_id, limit)


@router.post("/usage")
async def post_usage(request: Request, conn=Depends(get_conn)):
    try:
        raw = await request.json()
    except Exception:
        return {"recorded": False}
    if not isinstance(raw, dict) or not raw.get("event"):
        return {"recorded": False}
    ok = _usage.record(
        conn,
        str(raw.get("event")),
        raw.get("props") if isinstance(raw.get("props"), dict) else {},
        raw.get("device_id"),
        raw.get("app_version"),
    )
    return {"recorded": ok}


@router.get("/usage/export")
def usage_export(
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    mode: str = Query("raw", pattern="^(raw|daily)$"),
    conn=Depends(get_conn),
):
    text = _usage.export_csv(conn, since, until, mode)
    return Response(
        content=text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="usage-{mode}.csv"'},
    )


@router.get("/customers/{cid}")
def get_customer(cid: str, request: Request, conn=Depends(get_conn)):
    c = repo.customer_detail(conn, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    enabled = _rrn_on(conn)
    response = _scrub_customer(c, enabled)
    if enabled and response.get("rrn"):
        repo.log_rrn_access(
            conn,
            cid,
            "고객 상세 화면 열람",
            request.headers.get("X-Actor-Id") or "unknown",
            "detail_open",
        )
    return response


@router.patch("/customers/{cid}")
async def patch_customer(cid: str, request: Request, conn=Depends(get_conn)):
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="JSON 본문이 필요합니다.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="본문은 객체여야 합니다.")
    before = conn.execute(
        "SELECT rrn_hash, import_batch_id FROM customers WHERE id = ?", (cid,)
    ).fetchone()
    enabled = _rrn_on(conn)
    if not enabled:
        raw.pop("rrn", None)  # 토글 OFF: 모델 검증 전에 제거
    try:
        body = CustomerPatch(**raw)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=_val_detail(e))
    if body.rrn:
        dup = repo.find_customer_id_by_rrn(conn, body.rrn, exclude_id=cid)
        if dup:
            raise HTTPException(
                status_code=409,
                detail=f"같은 주민등록번호의 고객이 이미 있습니다 (id={dup}).",
            )
    try:
        c = repo.update_customer(conn, cid, body.model_dump(exclude_unset=True))
    except repo.DuplicateRRNError:
        raise HTTPException(
            status_code=409, detail="같은 주민등록번호의 고객이 이미 있습니다"
        )
    if c is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    bid = _import_batch_id(request)
    if (
        bid and "rrn" in raw and body.rrn and before
        and before["rrn_hash"] is None and before["import_batch_id"] != bid
    ):
        new_hash = conn.execute(
            "SELECT rrn_hash FROM customers WHERE id = ?", (cid,)
        ).fetchone()["rrn_hash"]
        repo.mark_batch_rrn(
            conn, bid, cid, new_hash, datetime.now(timezone.utc).isoformat()
        )
    response = _scrub_customer(c, enabled)
    if enabled and response.get("rrn"):
        repo.log_rrn_access(
            conn,
            cid,
            "고객 저장 응답에 주민등록번호 포함",
            request.headers.get("X-Actor-Id") or "unknown",
            "update_response",
        )
    changed = set(body.model_dump(exclude_unset=True)) - {"rrn"}
    if changed:
        _audit(conn, request, action="update", entity="customer", entity_id=cid, customer_id=cid, fields=",".join(sorted(changed)))
    return response


@router.delete("/customers/{cid}", status_code=204)
def delete_customer(cid: str, request: Request, conn=Depends(get_conn)):
    if not repo.delete_customer(conn, cid):
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    _audit(conn, request, action="delete", entity="customer", entity_id=cid, customer_id=cid)


@router.get("/customers/{cid}/audit")
def customer_audit(cid: str, limit: int = Query(200, ge=1, le=1000), conn=Depends(get_conn)):
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return repo.list_audit(conn, cid, limit)


@router.get("/customers/{cid}/rrn")
def reveal_customer_rrn(
    cid: str,
    request: Request,
    purpose: str = Query(..., min_length=2, description="조회 사유 (접근 로그에 기록됨)"),
    conn=Depends(get_conn),
):
    """주민등록번호 전체값 조회. 매 호출이 rrn_access_log 에 기록된다."""
    if not _rrn_on(conn):
        raise HTTPException(status_code=404, detail="주민등록번호 기능이 비활성화되어 있습니다.")
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    revealed = repo.reveal_rrn(
        conn, cid, purpose, request.headers.get("X-Actor-Id") or "unknown"
    )
    if revealed is None:
        raise HTTPException(status_code=404, detail="이 고객에게 저장된 주민등록번호가 없습니다.")
    return revealed


@router.get("/customers/{cid}/rrn-access-log")
def rrn_access_log(cid: str, conn=Depends(get_conn)):
    if not _rrn_on(conn):
        raise HTTPException(status_code=404, detail="주민등록번호 기능이 비활성화되어 있습니다.")
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return {"access_log": repo.list_rrn_access(conn, cid)}


_POLICY_MEANINGFUL = (
    "insurer", "product_name", "policy_number", "premium",
    "start_date", "end_date", "insured_period", "payment_period", "memo",
)


@router.post("/customers/{cid}/policies", status_code=201)
def add_policy(cid: str, body: PolicyIn, request: Request, conn=Depends(get_conn)):
    data = body.model_dump()
    if not any(str(data.get(k) or "").strip() for k in _POLICY_MEANINGFUL):
        raise HTTPException(
            status_code=422,
            detail="보험사·상품명 중 최소 하나는 입력해야 계약을 추가할 수 있습니다.",
        )
    p = repo.create_policy(conn, cid, data)
    if p is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    bid = _import_batch_id(request)
    if bid:
        repo.stamp_import_batch(conn, "policies", p["id"], bid)
    _audit(conn, request, action="create", entity="policy", entity_id=p["id"], customer_id=cid)
    return p


@router.get("/customers/{cid}/policies")
def list_policies(cid: str, conn=Depends(get_conn)):
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return {"policies": repo.list_policies(conn, cid)}


@router.patch("/policies/{pid}")
def patch_policy(pid: str, body: PolicyPatch, request: Request, conn=Depends(get_conn)):
    changed = body.model_dump(exclude_unset=True)
    p = repo.update_policy(conn, pid, changed)
    if p is None:
        raise HTTPException(status_code=404, detail="보험계약을 찾을 수 없습니다.")
    if changed:
        _audit(conn, request, action="update", entity="policy", entity_id=pid, customer_id=p["customer_id"], fields=",".join(sorted(changed)))
    return p


@router.delete("/policies/{pid}", status_code=204)
def delete_policy(pid: str, request: Request, conn=Depends(get_conn)):
    p = repo.get_policy(conn, pid)
    if not repo.delete_policy(conn, pid):
        raise HTTPException(status_code=404, detail="보험계약을 찾을 수 없습니다.")
    _audit(conn, request, action="delete", entity="policy", entity_id=pid, customer_id=p["customer_id"])


@router.post("/customers/{cid}/consultations", status_code=201)
def add_consultation(cid: str, body: ConsultationIn, request: Request, conn=Depends(get_conn)):
    k = repo.create_consultation(conn, cid, body.model_dump())
    if k is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    bid = _import_batch_id(request)
    if bid:
        repo.stamp_import_batch(conn, "consultations", k["id"], bid)
    _audit(conn, request, action="create", entity="consultation", entity_id=k["id"], customer_id=cid)
    return k


@router.get("/customers/{cid}/consultations")
def list_consultations(cid: str, conn=Depends(get_conn)):
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return {"consultations": repo.list_consultations(conn, cid)}


def _summary_to_content(s: dict) -> str:
    blocks = [s.get("summary") or ""]
    if s.get("key_points"):
        blocks.append("[핵심]\n- " + "\n- ".join(s["key_points"]))
    if s.get("customer_interests"):
        blocks.append("[관심 상품·보장]\n- " + "\n- ".join(s["customer_interests"]))
    if s.get("action_items"):
        blocks.append("[후속 할 일]\n- " + "\n- ".join(s["action_items"]))
    return "\n\n".join(b for b in blocks if b).strip()


def _looks_like_date(v: Optional[str]) -> bool:
    """'YYYY-MM-DD' 또는 'YYYY-MM-DDTHH:MM(:SS)' 같은 ISO-ish 날짜인지 느슨하게 검사."""
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$", (v or "").strip()))


@router.post("/customers/{cid}/consultations/from-audio", status_code=201)
async def consultation_from_audio(
    cid: str,
    request: Request,
    file: UploadFile = File(...),
    channel: str = Form("전화 녹취"),
    note: str = Form(""),           # 업로드 시 남기는 메모 (누구와의 통화인지 등). content 앞에 붙는다.
    consulted_at: str = Form(""),   # 녹취 날짜 (ISO-ish). 주면 now 대신 사용.
    conn=Depends(get_conn),
):
    """녹취 파일 → (Whisper) 전사 → (LLM) 요약 → 상담 이력 1건으로 저장."""
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")

    fn_phone, fn_dt = _parse_call_filename(file.filename)
    warnings = []
    if fn_phone is None and fn_dt is None:
        warnings.append("파일명에서 전화번호·통화일시를 읽지 못했습니다. 상담 저장 시 직접 입력해 주세요.")

    data = await read_upload_capped(file, MAX_AUDIO)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    suffix = os.path.splitext(file.filename or "audio.m4a")[1] or ".m4a"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        from whisper import summarize, transcribe

        stt = transcribe(tmp.name)
        if not stt["text"].strip():
            raise HTTPException(status_code=422, detail="전사 결과가 비어 있습니다 (무음/인식 실패).")
        summary = summarize(stt["text"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"녹취 처리 실패: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    note = (note or "").strip()
    content = _summary_to_content(summary)
    title = summary["title"]
    if note:
        content = f"[업로드 메모] {note}\n\n" + content
        if not (title or "").strip():
            title = note.splitlines()[0][:80]
    consult_body = {
        "channel": channel,
        "title": title,
        "content": content,
        "transcript": stt["text"],
        "follow_up_at": summary["follow_up_at"],
    }
    if _looks_like_date(consulted_at):
        consult_body["consulted_at"] = consulted_at.strip()
    consult = repo.create_consultation(conn, cid, consult_body)
    _audit(conn, request, action="create", entity="consultation", entity_id=consult["id"], customer_id=cid)
    return {
        "consultation": consult,
        "summary": summary,
        "transcript": stt["text"],
        "audio_minutes": stt["audio_minutes"],
        "warnings": warnings,
    }


@router.patch("/consultations/{kid}")
def patch_consultation(kid: str, body: ConsultationPatch, request: Request, conn=Depends(get_conn)):
    changed = body.model_dump(exclude_unset=True)
    k = repo.update_consultation(conn, kid, changed)
    if k is None:
        raise HTTPException(status_code=404, detail="상담 이력을 찾을 수 없습니다.")
    if changed:
        _audit(conn, request, action="update", entity="consultation", entity_id=kid, customer_id=k["customer_id"], fields=",".join(sorted(changed)))
    return k


@router.delete("/consultations/{kid}", status_code=204)
def delete_consultation(kid: str, request: Request, conn=Depends(get_conn)):
    k = repo.get_consultation(conn, kid)
    if not repo.delete_consultation(conn, kid):
        raise HTTPException(status_code=404, detail="상담 이력을 찾을 수 없습니다.")
    _audit(conn, request, action="delete", entity="consultation", entity_id=kid, customer_id=k["customer_id"])


@router.get("/follow-ups")
def follow_ups(
    until: Optional[str] = Query(None, description="이 날짜(포함)까지. 기본 = 오늘+7일"),
    conn=Depends(get_conn),
):
    cutoff = until or (date.today() + timedelta(days=7)).isoformat()
    return {"until": cutoff, "follow_ups": repo.upcoming_follow_ups(conn, cutoff)}


@router.get("/dashboard")
def dashboard(
    follow_up_days: int = Query(7, ge=1, le=90),
    expiry_days: int = Query(30, ge=1, le=365),
    birthday_days: int = Query(30, ge=1, le=365),
    recent: int = Query(5, ge=1, le=20),
    own: Optional[int] = Query(None),  # 1 이면 계약 파생 수치(계약 수·만기 임박)를 '내가 가입시킴'으로 한정
    conn=Depends(get_conn),
):
    """홈 화면 한 번에: 건수 / 후속 연락 예정(미완료) / 만기 임박 계약 / 생일 임박 / 최근 상담.
    own=1 이면 계약 수·만기 임박 목록만 is_own 계약으로 좁힌다 (고객 수·생일은 전역 유지)."""
    today = date.today()
    return {
        "counts": repo.dashboard_counts(conn, own=own),
        "follow_up_until": (today + timedelta(days=follow_up_days)).isoformat(),
        "follow_ups": repo.upcoming_follow_ups(
            conn, (today + timedelta(days=follow_up_days)).isoformat()
        ),
        "expiry_until": (today + timedelta(days=expiry_days)).isoformat(),
        "expiring_policies": repo.expiring_policies(
            conn, (today + timedelta(days=expiry_days)).isoformat(), own=own
        ),
        "birthday_until": (today + timedelta(days=birthday_days)).isoformat(),
        "birthdays": repo.upcoming_birthdays(conn, birthday_days),
        "recent_consultations": repo.recent_consultations(conn, recent),
    }


@router.get("/management")
def management(
    expiry_days: int = Query(30, ge=1, le=365),
    payment_days: int = Query(60, ge=1, le=365),
    birthday_days: int = Query(30, ge=1, le=365),
    follow_up_days: int = Query(14, ge=1, le=365),
    conn=Depends(get_conn),
):
    """만기·납입종료·생일·후속연락(예정/연체)을 한 번에. 고객목록 탭에서 쓰라고 유지."""
    today = date.today()
    tstr = today.isoformat()
    fu_until = (today + timedelta(days=follow_up_days)).isoformat()
    allf = repo.upcoming_follow_ups(conn, fu_until)
    return {
        "expiring_policies": repo.expiring_policies(
            conn, (today + timedelta(days=expiry_days)).isoformat()
        ),
        "payment_ending": repo.payment_ending_policies(
            conn, (today + timedelta(days=payment_days)).isoformat()
        ),
        "birthdays": repo.upcoming_birthdays(conn, birthday_days),
        "follow_ups": [f for f in allf if (f.get("follow_up_at") or "") >= tstr],
        "overdue_follow_ups": [f for f in allf if (f.get("follow_up_at") or "") < tstr],
    }


@router.post("/consultations/{kid}/follow-up/complete")
def complete_follow_up(kid: str, request: Request, conn=Depends(get_conn)):
    k = repo.complete_follow_up(conn, kid)
    if k is None:
        raise HTTPException(status_code=404, detail="상담 이력을 찾을 수 없습니다.")
    _audit(conn, request, action="update", entity="consultation", entity_id=kid, customer_id=k["customer_id"], fields="follow_up_done_at")
    return k


@router.post("/consultations/{kid}/follow-up/reopen")
def reopen_follow_up(kid: str, request: Request, conn=Depends(get_conn)):
    k = repo.reopen_follow_up(conn, kid)
    if k is None:
        raise HTTPException(status_code=404, detail="상담 이력을 찾을 수 없습니다.")
    _audit(conn, request, action="update", entity="consultation", entity_id=kid, customer_id=k["customer_id"], fields="follow_up_done_at")
    return k


@router.post("/maintenance/recompute-expiry")
def recompute_expiry(conn=Depends(get_conn)):
    """전 계약 백필: 보험기간(insured_period, 없으면 memo에서 추출) 기반으로 만기/납입종료일
    재산출. 수동 입력(end_date_derived==0)은 건너뜀. 부팅 자동 실행 없음. 멱등."""
    return repo.recompute_all_expiry(conn)


# ---------- 고객 ↔ 약관 연결 (작업 E 결합) ----------

@router.post("/policies/{pid}/document")
async def attach_policy_document(
    pid: str,
    request: Request,
    file: UploadFile = File(...),
    note: str = Form(""),  # 이 약관 파일 메모 (선택). 계약 memo 에 덧붙인다 (기존 memo 유지).
    conn=Depends(get_conn),
):
    """보험계약에 약관 PDF 를 올린다: 파싱 → RAG 색인 → policies.document_id 설정."""
    existing_policy = repo.get_policy(conn, pid)
    if existing_policy is None:
        raise HTTPException(status_code=404, detail="보험계약을 찾을 수 없습니다.")

    name = file.filename or "policy.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="PDF 파일만 지원합니다.")
    data = await read_upload_capped(file, MAX_PDF)
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    try:
        parsed = parse_pdf_bytes(data, filename=name).to_dict()
        indexed = index_parsed_doc(parsed)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"약관 처리 실패: {e}")

    policy = repo.set_policy_document(conn, pid, parsed["doc_id"])
    if (note or "").strip():
        prev = (repo.get_policy(conn, pid) or {}).get("memo") or ""
        new_memo = f"{prev}\n[약관 메모] {note.strip()}" if prev else f"[약관 메모] {note.strip()}"
        policy = repo.update_policy(conn, pid, {"memo": new_memo}) or policy
    fields = "document_id,memo" if (note or "").strip() else "document_id"
    _audit(conn, request, action="update", entity="policy", entity_id=pid, customer_id=existing_policy["customer_id"], fields=fields)
    return {"policy": policy, "indexed": indexed}


@router.delete("/policies/{pid}/document", status_code=204)
def detach_policy_document(pid: str, request: Request, conn=Depends(get_conn)):
    """연결만 해제한다. RAG 색인 자체는 다른 계약이 참조할 수 있어 남겨둔다."""
    policy = repo.get_policy(conn, pid)
    if repo.set_policy_document(conn, pid, None) is None:
        raise HTTPException(status_code=404, detail="보험계약을 찾을 수 없습니다.")
    _audit(conn, request, action="update", entity="policy", entity_id=pid, customer_id=policy["customer_id"], fields="document_id")


@router.get("/customers/{cid}/ask")
def ask_customer_documents(
    cid: str,
    q: str = Query(..., min_length=1),
    top_k: int = Query(5, ge=1, le=20),
    conn=Depends(get_conn),
):
    """이 고객의 보험계약에 연결된 약관들만 대상으로 질문한다."""
    if repo.get_customer(conn, cid) is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")

    doc_ids = repo.customer_document_ids(conn, cid)
    if not doc_ids:
        return policy_qa.fallback_answer(conn, cid, q)

    result = rag_answer(q, top_k=top_k, doc_ids=doc_ids)
    if result.get("abstained"):
        fallback = policy_qa.fallback_answer(conn, cid, q)
        fallback["document_ids"] = doc_ids
        fallback["sources"] = result.get("sources", [])
        return fallback
    result.update({"basis": "clause", "fallback": False, "disclaimer": None,
                   "document_ids": doc_ids})
    return result


@router.get("/customers/{cid}/coverage-analysis")
def coverage_analysis(
    cid: str,
    audience: str = Query("internal", pattern="^(customer|internal)$"),
    include_sufficient: bool = Query(True),
    priority: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    conn=Depends(get_conn),
):
    """최신 completed 보장분석 run을 고객용/내부용으로 분리해 조회."""
    result = coverage.get_analysis_response(
        conn,
        cid,
        audience=audience,
        include_sufficient=include_sufficient,
        priority=priority,
        run_id=run_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="고객 또는 보장분석 결과를 찾을 수 없습니다.")
    return result


@router.post("/customers/{cid}/coverage-analysis/recalculate")
def recalculate_coverage_analysis(
    cid: str,
    body: dict = Body(default_factory=dict),
    conn=Depends(get_conn),
):
    """13개 카테고리 × 46개 세부 담보 보장분석을 동기 재계산하고 저장."""
    result = coverage.recalculate(conn, cid, body)
    if result is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return result


@router.patch("/customers/{cid}/coverage-analysis/{coverage_id}")
def patch_coverage_analysis_item(
    cid: str,
    coverage_id: str,
    body: dict = Body(...),
    conn=Depends(get_conn),
):
    """세부 담보 분석 결과를 수동 조정하고 manual_override를 기록."""
    result = coverage.patch_item(conn, cid, coverage_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="보장분석 항목을 찾을 수 없습니다.")
    return result


# ---------- 통계 API (EnhancedDashboard) ----------

@router.get("/statistics/summary")
def get_statistics_summary(
    period: str = Query("month"),
    year: int = Query(2026),
    month: Optional[int] = Query(None),
    conn=Depends(get_conn),
):
    """
    고객/계약/상담 통계 요약.
    
    Query params:
        - period: 'month' (기본값) / 'year' / 'all-time'
        - year: 연도 (기본값: 2026)
        - month: 월 (period='month'일 때 필요)
    
    Returns:
        {
            "period": "2026-09",
            "customers": {"total": 324, "new_this_period": 45, ...},
            "policies": {"total": 756, "active": 680, ...},
            "consultations": {"total": 1234, "this_period": 89, ...},
            "regional_top5": [...],
            "insurer_top5": [...]
        }
    """
    try:
        # period='month'일 때 month가 없으면 현재 월 사용
        if period == "month" and month is None:
            month = datetime.now().month
        
        return _stats.get_cached_summary(conn, period, year, month)
    except Exception as e:
        logger.exception("통계 요약 조회 실패")
        raise HTTPException(status_code=500, detail=f"통계 조회 실패: {str(e)}")


@router.get("/statistics/charts")
def get_statistics_charts(
    chart_type: str = Query("monthly_trend"),
    year: int = Query(2026),
    conn=Depends(get_conn),
):
    """
    차트 데이터 조회.
    
    Query params:
        - chart_type: 'monthly_trend' (기본값) / 'regional_pie' / 'insurer_bar'
        - year: 연도 (월별 추이용, 기본값: 2026)
    
    Returns:
        {
            "chart_type": "monthly_trend",
            "data": {
                "labels": ["2026-01", "2026-02", ...],
                "datasets": [
                    {"label": "신규 고객", "data": [12, 18, ...]},
                    {"label": "신규 계약", "data": [20, 28, ...]}
                ]
            }
        }
    """
    try:
        return _stats.get_cached_charts(conn, chart_type, year)
    except Exception as e:
        logger.exception("차트 데이터 조회 실패")
        raise HTTPException(status_code=500, detail=f"차트 조회 실패: {str(e)}")
