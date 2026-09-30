"""
AI 기반 CSV 컬럼/행 분류기.

Ollama qwen2.5:3b를 사용하여:
1. CSV 컬럼 헤더를 고객 필드로 자동 매핑
2. 행 데이터에서 필드 추출

규칙 기반 폴백 포함.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

try:
    import httpx  # type: ignore
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore
    HTTPX_AVAILABLE = False

from .prompts import classify, extract_consultation, extract_customer, extract_policy


# Ollama 설정
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 10.0

INSURER_KEYWORDS = (
    "삼성생명", "삼성화재", "kb", "kb손해보험", "현대해상", "한화생명",
    "메리츠", "db손해보험", "동부", "롯데", "교보생명", "신한라이프",
    "흥국", "농협", "nh", "aia", "푸본", "라이나", "보험",
)
CONSULTATION_KEYWORDS = ("상담", "방문", "전화", "통화", "카톡", "카카오톡", "후속", "연락")


def normalize_date(value) -> Optional[str]:
    """여러 엑셀 날짜 표현을 YYYY-MM-DD로 정규화."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    else:
        text = re.sub(r"\s+", "", text)
        m = re.fullmatch(r"(\d{4})[./-](\d{1,2})[./-]?(\d{1,2})", text)
        if m:
            text = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def normalize_phone(value) -> Optional[str]:
    """전화번호를 하이픈 포함 형식으로 정규화."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:
        if digits.startswith("02"):
            return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return None


def normalize_gender(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("여", "여자", "f", "female"):
        return "F"
    if text in ("남", "남자", "m", "male"):
        return "M"
    return None


def normalize_amount(value) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    manwon = re.search(r"(\d+(?:\.\d+)?)\s*만원", text)
    if manwon:
        return int(float(manwon.group(1)) * 10000)
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _cells(row_data: list) -> list[str]:
    return [str(v).strip() for v in row_data if v is not None and str(v).strip()]


def fallback_classify(row_data: list) -> dict:
    """Ollama 실패 시 고객/보험계약/상담일지를 규칙 기반으로 분류."""
    cells = _cells(row_data)
    text = " ".join(cells).lower()
    has_phone = any(normalize_phone(cell) for cell in cells)
    has_birth = any(normalize_date(cell) and re.sub(r"\D", "", cell).startswith(("19", "20")) for cell in cells)
    has_gender = any(normalize_gender(cell) for cell in cells)
    insurer_hits = sum(1 for keyword in INSURER_KEYWORDS if keyword in text)
    has_premium = any(normalize_amount(cell) and ("원" in cell or "만" in cell) for cell in cells)
    consult_hits = sum(1 for keyword in CONSULTATION_KEYWORDS if keyword in text)

    if insurer_hits and (has_premium or any("납" in cell or "증권" in cell for cell in cells)):
        return {"category": "보험계약", "confidence": 0.82, "reasoning": "보험사/보험료/납입 정보 감지 (규칙)"}
    if consult_hits >= 2 or (consult_hits and any(normalize_date(cell) for cell in cells)):
        return {"category": "상담일지", "confidence": 0.72, "reasoning": "상담 채널/내용/일자 감지 (규칙)"}
    if has_phone or (has_birth and has_gender):
        return {"category": "고객", "confidence": 0.72, "reasoning": "전화번호/생년월일/성별 감지 (규칙)"}
    if insurer_hits:
        return {"category": "보험계약", "confidence": 0.62, "reasoning": "보험 키워드 감지 (규칙)"}
    return {"category": "고객", "confidence": 0.3, "reasoning": "분류 불가 (기본값: 고객)"}


def extract_customer_rule(row_data: list) -> dict:
    """고객 행 필드 추출 폴백. insurance-schedule-agent 11열 형식을 우선 지원."""
    cells = _cells(row_data)
    item: dict[str, Any] = {"name": None, "phone": None, "birth_date": None, "gender": None, "email": None, "address": None, "memo": None}
    if len(row_data) >= 11:
        item.update({
            "name": str(row_data[5]).strip() or None,
            "phone": normalize_phone(row_data[6]),
            "birth_date": normalize_date(row_data[8]),
            "gender": normalize_gender(row_data[9]),
            "address": " ".join(_cells([row_data[2], row_data[7]])) or None,
            "memo": str(row_data[10]).strip() or None,
        })
        return item
    for cell in cells:
        if item["phone"] is None:
            item["phone"] = normalize_phone(cell)
        if item["birth_date"] is None:
            item["birth_date"] = normalize_date(cell)
        if item["gender"] is None:
            item["gender"] = normalize_gender(cell)
        if item["email"] is None and "@" in cell:
            item["email"] = cell
    ignored = {v for v in (item["phone"], item["birth_date"], item["gender"], item["email"]) if v}
    for cell in cells:
        if cell in ignored or normalize_phone(cell) or normalize_date(cell) or normalize_gender(cell) or "@" in cell:
            continue
        if item["name"] is None and re.fullmatch(r"[가-힣]{2,5}|[A-Za-z ]{2,40}", cell):
            item["name"] = cell
        elif item["address"] is None and any(token in cell for token in ("시", "군", "구", "도", "읍", "면", "동", "로")):
            item["address"] = cell
        elif item["memo"] is None:
            item["memo"] = cell
    return item


def extract_policy_rule(row_data: list) -> dict:
    """보험계약 행 필드 추출 폴백."""
    cells = _cells(row_data)
    item: dict[str, Any] = {
        "insurer": None, "product_name": None, "policy_number": None, "plan_type": None,
        "premium": None, "payment_cycle": None, "start_date": None, "end_date": None,
        "status": "ACTIVE", "memo": None,
    }
    for cell in cells:
        low = cell.lower()
        if item["insurer"] is None and any(k in low for k in INSURER_KEYWORDS):
            item["insurer"] = cell
            continue
        if item["premium"] is None and ("원" in cell or "만" in cell):
            item["premium"] = normalize_amount(cell)
            continue
        if item["payment_cycle"] is None:
            if cell in ("월납", "월", "매월", "monthly"):
                item["payment_cycle"] = "MONTHLY"; continue
            if cell in ("년납", "연납", "년", "yearly"):
                item["payment_cycle"] = "YEARLY"; continue
            if cell in ("일시납", "lumpsum"):
                item["payment_cycle"] = "LUMPSUM"; continue
        dt = normalize_date(cell)
        if dt and item["start_date"] is None:
            item["start_date"] = dt; continue
        if dt and item["end_date"] is None:
            item["end_date"] = dt; continue
        if cell in ("유지", "정상", "ACTIVE"):
            item["status"] = "ACTIVE"; continue
        if cell in ("해지", "취소", "CANCELLED"):
            item["status"] = "CANCELLED"; continue
        if cell in ("실효", "LAPSED"):
            item["status"] = "LAPSED"; continue
        if cell in ("만기", "EXPIRED"):
            item["status"] = "EXPIRED"; continue
        if item["policy_number"] is None and re.fullmatch(r"[A-Za-z0-9-]{8,}", cell):
            item["policy_number"] = cell; continue
        if item["product_name"] is None and ("보험" in cell or "플랜" in cell or "종신" in cell):
            item["product_name"] = cell
        elif item["memo"] is None:
            item["memo"] = cell
    return item


def extract_consultation_rule(row_data: list) -> dict:
    """상담일지 행 필드 추출 폴백."""
    cells = _cells(row_data)
    item: dict[str, Any] = {"consulted_at": None, "channel": "기타", "title": None, "content": None, "follow_up_at": None}
    content_parts: list[str] = []
    for cell in cells:
        dt = normalize_date(cell)
        if dt and item["consulted_at"] is None:
            item["consulted_at"] = dt; continue
        if dt and item["follow_up_at"] is None:
            item["follow_up_at"] = dt; continue
        if cell in ("방문", "전화", "카톡", "이메일"):
            item["channel"] = cell; continue
        if cell in ("통화", "휴대폰"):
            item["channel"] = "전화"; continue
        if cell in ("카카오톡", "문자"):
            item["channel"] = "카톡"; continue
        content_parts.append(cell)
    content = " ".join(content_parts).strip() or None
    item["content"] = content
    item["title"] = content[:20] if content else None
    return item


def _extract_json(text: str) -> Optional[dict]:
    """응답에서 JSON 추출 (```json ... ``` 제거)."""
    if not text:
        return None
    
    # ```json ... ``` 형식 제거
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    # {...} 추출
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def classify_column_ai(column_name: str) -> Optional[dict]:
    """
    AI로 컬럼 이름을 고객 필드로 매핑.
    
    Returns:
        {"field": "name", "confidence": 0.95} 또는 None (실패 시)
    """
    if not HTTPX_AVAILABLE:
        return None
    
    prompt = classify.classify_column_prompt(column_name)
    
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:  # type: ignore
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "num_predict": 100
                    }
                }
            )
            
            if response.status_code != 200:
                return None
            
            result = response.json()
            output_text = result.get("response", "").strip()
            
            return _extract_json(output_text)
    
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):  # type: ignore
        return None


def classify_column_rule(column_name: str) -> Optional[str]:
    """
    규칙 기반 컬럼 분류 (폴백).
    
    Returns:
        필드명 또는 None
    """
    if not column_name:
        return None
    
    name_lower = column_name.lower().strip()
    
    # 매핑 규칙
    if any(kw in name_lower for kw in ["이름", "name", "고객명", "성명", "성함"]):
        return "name"
    if any(kw in name_lower for kw in ["전화", "phone", "연락", "휴대폰", "핸드폰", "h.p", "hp", "mobile", "tel"]):
        return "phone"
    if any(kw in name_lower for kw in ["생년월일", "생일", "birth", "birthday"]):
        return "birth_date"
    if any(kw in name_lower for kw in ["성별", "gender", "sex"]):
        return "gender"
    if any(kw in name_lower for kw in ["이메일", "email", "메일", "mail"]):
        return "email"
    # "지역"은 insurance-schedule-agent 11열 형식에서 광역/기초 주소 보조 컬럼이다.
    # "상세 주소"가 함께 있으면 그 컬럼이 실제 address로 매핑되어야 하므로
    # 단독 주소 신호(주소/address)만 우선 매핑하고, "지역"은 AI 보조/폴백에 맡긴다.
    if any(kw in name_lower for kw in ["주소", "address"]):
        return "address"
    if any(kw in name_lower for kw in ["직업", "occupation", "job"]):
        return "occupation"
    if any(kw in name_lower for kw in ["태그", "tag", "분류", "category"]):
        return "tags"
    if any(kw in name_lower for kw in ["메모", "memo", "비고", "특이사항", "note"]):
        return "memo"
    if any(kw in name_lower for kw in ["상태", "status"]):
        return "customer_status"
    
    return None


async def auto_map_columns(columns: list[str]) -> dict[str, str]:
    """
    CSV 컬럼 목록을 고객 필드로 자동 매핑.
    
    AI 시도 → 규칙 기반 폴백
    
    Returns:
        {고객필드: CSV컬럼명}
    """
    mapping = {}
    
    for col in columns:
        if not col or not col.strip():
            continue
        
        # AI 시도
        ai_result = None
        if HTTPX_AVAILABLE:
            try:
                ai_result = await classify_column_ai(col)
            except Exception:
                pass
        
        if ai_result and ai_result.get("field") and ai_result.get("confidence", 0) >= 0.6:
            field = ai_result["field"]
            if field and field not in mapping:
                mapping[field] = col
            continue
        
        # 규칙 기반 폴백
        field = classify_column_rule(col)
        if field and field not in mapping:
            mapping[field] = col
    
    return mapping


async def extract_customer_ai(row_data: list) -> Optional[dict]:
    """
    AI로 고객 행 데이터에서 필드 추출.
    
    Returns:
        {"name": "강소임", "phone": "010-9430-1522", ...} 또는 None
    """
    if not HTTPX_AVAILABLE:
        return None
    
    prompt = extract_customer.extract_customer_prompt(row_data)
    
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:  # type: ignore
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "num_predict": 300
                    }
                }
            )
            
            if response.status_code != 200:
                return None
            
            result = response.json()
            output_text = result.get("response", "").strip()
            
            return _extract_json(output_text)
    
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):  # type: ignore
        return None


async def classify_row_ai(row_data: list, row_index: int = 0) -> Optional[dict]:
    """Ollama로 행을 고객/보험계약/상담일지 중 하나로 분류."""
    if not HTTPX_AVAILABLE:
        return None
    prompt = classify.classify_row_prompt(row_data, row_index)
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:  # type: ignore
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 150},
                },
            )
        if response.status_code != 200:
            return None
        parsed = _extract_json(response.json().get("response", "").strip())
        if not parsed or parsed.get("category") not in ("고객", "보험계약", "상담일지"):
            return None
        confidence = float(parsed.get("confidence", 0.0))
        if confidence < 0.0 or confidence > 1.0:
            return None
        return parsed
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError, ValueError):  # type: ignore
        return None


async def extract_policy_ai(row_data: list) -> Optional[dict]:
    """Ollama로 보험계약 행 필드 추출."""
    if not HTTPX_AVAILABLE:
        return None
    prompt = extract_policy.extract_policy_prompt(row_data)
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:  # type: ignore
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 300},
                },
            )
        if response.status_code != 200:
            return None
        return _extract_json(response.json().get("response", "").strip())
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):  # type: ignore
        return None


async def extract_consultation_ai(row_data: list) -> Optional[dict]:
    """Ollama로 상담일지 행 필드 추출."""
    if not HTTPX_AVAILABLE:
        return None
    prompt = extract_consultation.extract_consultation_prompt(row_data)
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:  # type: ignore
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 300},
                },
            )
        if response.status_code != 200:
            return None
        return _extract_json(response.json().get("response", "").strip())
    except (httpx.TimeoutException, httpx.RequestError, json.JSONDecodeError):  # type: ignore
        return None


async def classify_and_extract_row(row_data: list, row_index: int = 0) -> dict:
    """1차 분류 후 카테고리별 필드 추출. AI 실패 시 규칙 기반으로 폴백."""
    classification = await classify_row_ai(row_data, row_index) or fallback_classify(row_data)
    category = classification["category"]
    if category == "고객":
        extracted = await extract_customer_ai(row_data) or extract_customer_rule(row_data)
        ai_category = "customer"
    elif category == "보험계약":
        extracted = await extract_policy_ai(row_data) or extract_policy_rule(row_data)
        ai_category = "policy"
    else:
        extracted = await extract_consultation_ai(row_data) or extract_consultation_rule(row_data)
        ai_category = "consultation"
    return {
        "row_index": row_index,
        "original_data": row_data,
        "ai_category": ai_category,
        "ai_confidence": classification.get("confidence", 0.0),
        "reasoning": classification.get("reasoning"),
        "extracted_data": extracted,
    }


async def classify_and_extract_batch(rows: list[list], batch_size: int = 50) -> list[dict]:
    """최대 50행 단위로 분류/추출한다. 호출부에서 진행률 표시용으로 사용."""
    results: list[dict] = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        for offset, row in enumerate(batch):
            results.append(await classify_and_extract_row(row, start + offset))
    return results


# 동기 버전 (기존 import_data.py와 호환)
def auto_map_columns_sync(columns: list[str]) -> dict[str, str]:
    """
    동기 버전 컬럼 자동 매핑 (규칙 기반만).
    
    Returns:
        {고객필드: CSV컬럼명}
    """
    mapping = {}
    
    for col in columns:
        if not col or not col.strip():
            continue
        
        field = classify_column_rule(col)
        if field and field not in mapping:
            mapping[field] = col
    
    return mapping
