"""
붙여넣은 자유 텍스트 → 고객 필드 추출 (LLM). 저장은 안 한다 — 미리보기용.

상담자가 이런 식으로 던진다:
    황화연
    010-4601-0151
    580824-2123511
    효동로 291 금호아파트 101-1054
    주부
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Optional

from . import rrn as rrn_util

# memo 안에 섞여 들어온 주민번호 패턴 (6자리-7자리 또는 붙어있는 13자리)
_RRN_IN_TEXT = re.compile(r"\b(\d{6})[-\s]?(\d{7})\b")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rrn_from_source(fields: dict, source_text: Optional[str]) -> None:
    """원본 텍스트에 완전한 13자리 주민번호가 있으면 그걸로 교정한다.
    LLM 이 자주 '910201' 은 생년월일로, '1621916' 만 rrn 으로 쪼개 놓거나
    뒷자리에 없는 숫자를 붙이기 때문에, 원문에 원본이 있으면 그게 최우선이다."""
    if not source_text:
        return
    hits = list(_RRN_IN_TEXT.finditer(source_text))
    if not hits:
        return
    fulls = [m.group(1) + m.group(2) for m in hits]
    cur = "".join(ch for ch in str(fields.get("rrn") or "") if ch.isdigit())
    if len(cur) == 13 and cur in fulls:
        return  # 이미 원문과 일치하는 완전값
    # 1) LLM 부분값이 원문의 완전값과 겹침 (앞부분 포함 또는 뒷 7자리 일치) → 승격
    if cur:
        for full in fulls:
            if cur in full or (len(cur) >= 7 and full[-7:] in cur):
                fields["rrn"] = full
                return
    # 2) 이름이 원문에서 어느 주민번호 바로 옆(±25자)에 있으면 그 번호 사용 (여러 명 케이스)
    name = str(fields.get("name") or "").strip()
    if name:
        for mm, full in zip(hits, fulls):
            window = source_text[max(0, mm.start() - 25):mm.end() + 25]
            if name in window:
                fields["rrn"] = full
                return
    # 3) LLM 이 못 뽑았고 원문에 주민번호가 딱 하나면 그걸 사용
    if not cur and len(set(fulls)) == 1:
        fields["rrn"] = fulls[0]


def _fix_birthdate(fields: dict) -> None:
    """생년월일 정규화. 주민번호가 있으면 그걸 우선(결정적). 없으면 LLM 값을 정리."""
    # 1) 주민번호에서 유도 — LLM 이 종종 세기(19/20)를 틀리므로 이게 최우선
    rrn = "".join(ch for ch in str(fields.get("rrn") or "") if ch.isdigit())
    if len(rrn) >= 7:
        century = "19" if rrn[6] in "1256" else "20"  # 1,2,5,6=1900년대 / 3,4,7,8=2000년대
        yy, mm, dd = rrn[0:2], rrn[2:4], rrn[4:6]
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31:
            fields["birth_date"] = f"{century}{yy}-{mm}-{dd}"
            return

    bd = str(fields.get("birth_date") or "").strip()
    if _ISO_DATE.match(bd):
        return
    digits = "".join(c for c in bd if c.isdigit())
    if len(digits) == 8 and 1900 <= int(digits[:4]) <= 2100:
        fields["birth_date"] = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        return
    if len(digits) == 6:  # YYMMDD — 세기 모호 → 1900년대로 가정
        fields["birth_date"] = f"19{digits[:2]}-{digits[2:4]}-{digits[4:6]}"
        return
    fields["birth_date"] = None


def _fix_gender(fields: dict) -> None:
    """주민번호 뒷자리 첫 숫자로 성별을 확정한다 (LLM 값보다 우선)."""
    rrn = "".join(ch for ch in str(fields.get("rrn") or "") if ch.isdigit())
    if len(rrn) >= 7:
        g = rrn[6]
        if g in "1357":
            fields["gender"] = "M"
        elif g in "2468":
            fields["gender"] = "F"

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# 필드 추출·요약은 단순 작업이라 작은 모델로 (RAG 답변용 14b 와 분리)
LLM_MODEL = os.environ.get("INTAKE_LLM_MODEL", "qwen2.5:7b")
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")  # 모델을 메모리에 유지

_FIELDS = [
    "name", "phone", "birth_date", "gender", "email", "address", "occupation",
    "rrn", "memo",
]

_PROMPT = """다음은 보험 상담자가 적어 보낸 고객 메모다. 아래 필드를 추출해 JSON 하나만 출력하라.
찾을 수 없는 값은 null. 지어내지 마라.

- name: 이름
- phone: 전화번호 (숫자와 하이픈만)
- birth_date: 생년월일 YYYY-MM-DD. 주민등록번호 앞 6자리로 추정하되, 뒷자리 첫 숫자가
  1·2면 1900년대, 3·4면 2000년대다.
- gender: 주민등록번호 뒷자리 첫 숫자가 1·3이면 "M", 2·4면 "F"
- email
- address: 주소. 전체 도로명 주소가 아니어도, 지역명·시·군·구·동 등 위치 단서가
  하나라도 있으면 그대로 넣어라 (예: "전남 무안군", "부산", "강남구"). 완전하지 않다고
  null 로 두지 마라.
- occupation: 직업 (예: 주부, 회사원, 자영업)
- rrn: 주민등록번호. "6자리-7자리" 또는 13자리 숫자 형태. 잘려 있어도(예: 620805-1)
  그대로 rrn 에 넣어라. 주민등록번호는 절대 memo 에 넣지 마라.
- memo: 위 어느 필드에도 해당하지 않는 내용만 (예: 보험료 규모, 상담 특이사항).
  이름·전화·주소·직업·주민번호는 여기 넣지 마라. 없으면 null

출력 형식:
{"name": null, "phone": null, "birth_date": null, "gender": null, "email": null, "address": null, "occupation": null, "rrn": null, "memo": null}

[고객 메모]
"""


def _call_llm(prompt: str, model: str) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": KEEP_ALIVE,
        # num_ctx 는 워밍업(main._warm_ollama)과 반드시 같은 값이어야 재로딩이 안 생긴다.
        "options": {"temperature": 0, "num_predict": 1536, "num_ctx": 4096},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return json.loads((data.get("response") or "").strip())
    except json.JSONDecodeError:
        return {}


_BULK_PROMPT = """다음 텍스트에는 여러 명의 고객 정보가 줄 또는 문단 단위로 들어 있다.
각 고객을 아래 형식의 JSON 배열로 추출하라. 한 명도 빠뜨리지 마라. 없는 값은 null.
주민등록번호는 rrn 에만 넣고 memo 에는 넣지 마라. 이름·전화·주소·직업도 memo 금지.

출력 형식:
{"customers": [
  {"name": null, "phone": null, "birth_date": null, "gender": null, "email": null, "address": null, "occupation": null, "rrn": null, "memo": null}
]}

address 는 지역명·시·군·구·동만 있어도 그대로 넣는다 (완전한 주소 아니어도 null 금지).
birth_date 는 반드시 "YYYY-MM-DD" 형식. 주민번호 앞 6자리로 만들되
뒷자리 첫 숫자 1·2면 19+앞2자리, 3·4면 20+앞2자리. 예: 620805-1 → "1962-08-05".
gender 는 뒷자리 첫 숫자 1·3="M", 2·4="F".

[고객 목록]
"""


def _normalize(raw: dict, source_text: Optional[str] = None) -> dict[str, Any]:
    """LLM 이 뽑은 한 명치 dict → {fields, warnings}. 단건/일괄 공통."""
    fields: dict[str, Any] = {k: (raw.get(k) or None) for k in _FIELDS}
    warnings: list[str] = []

    if fields.get("phone"):
        digits = "".join(ch for ch in str(fields["phone"]) if ch.isdigit())
        if len(digits) == 11:
            fields["phone"] = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"

    memo_val = str(fields.get("memo") or "")
    m = _RRN_IN_TEXT.search(memo_val)
    if m:
        if not fields.get("rrn"):
            fields["rrn"] = m.group(1) + m.group(2)
        cleaned = _RRN_IN_TEXT.sub("", memo_val).strip(" ,·\n\t")
        fields["memo"] = cleaned or None

    _rrn_from_source(fields, source_text)

    if fields.get("rrn"):
        try:
            fields["rrn"] = rrn_util.normalize(fields["rrn"])
        except ValueError as e:
            warnings.append(f"주민등록번호 형식 확인 필요: {e}")
            digits = "".join(ch for ch in str(fields["rrn"]) if ch.isdigit())
            fields["rrn"] = digits or None

    _fix_birthdate(fields)
    _fix_gender(fields)

    if not fields.get("name"):
        warnings.append("이름을 찾지 못했습니다. 직접 입력하세요.")

    return {"fields": fields, "warnings": warnings}


def extract_multiple(text: str, model: Optional[str] = None) -> list[dict]:
    """여러 명치 텍스트 → [{fields, warnings}, ...]. LLM 1회 호출."""
    model = model or LLM_MODEL
    raw = _call_llm(_BULK_PROMPT + text.strip(), model)
    rows = raw.get("customers") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    return [_normalize(r, text) for r in rows if isinstance(r, dict)]


def extract_customer_fields(text: str, model: Optional[str] = None) -> dict[str, Any]:
    model = model or LLM_MODEL
    raw = _call_llm(_PROMPT + text.strip(), model)

    fields: dict[str, Any] = {k: (raw.get(k) or None) for k in _FIELDS}
    warnings: list[str] = []

    if fields.get("phone"):
        digits = "".join(ch for ch in str(fields["phone"]) if ch.isdigit())
        if len(digits) == 11:
            fields["phone"] = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"

    # 안전장치: 모델이 주민번호를 memo 에 넣었으면 rrn 으로 옮기고 memo 에서 지운다.
    memo_val = str(fields.get("memo") or "")
    m = _RRN_IN_TEXT.search(memo_val)
    if m:
        if not fields.get("rrn"):
            fields["rrn"] = m.group(1) + m.group(2)
        cleaned = _RRN_IN_TEXT.sub("", memo_val).strip(" ,·\n\t")
        fields["memo"] = cleaned or None

    _rrn_from_source(fields, text)

    if fields.get("rrn"):
        try:
            fields["rrn"] = rrn_util.normalize(fields["rrn"])
        except ValueError as e:
            warnings.append(f"주민등록번호 형식 확인 필요: {e}")
            digits = "".join(ch for ch in str(fields["rrn"]) if ch.isdigit())
            fields["rrn"] = digits or None

    _fix_birthdate(fields)
    _fix_gender(fields)

    if not fields.get("name"):
        warnings.append("이름을 찾지 못했습니다. 직접 입력하세요.")

    return {"fields": fields, "warnings": warnings, "model": model}


_POLICY_PROMPT = """다음은 보험 가입제안서/청약서/증권 을 OCR 한 텍스트다. 계약(증권) 정보를 뽑아 아래 JSON 하나만 출력하라.
문서에 없으면 null. 절대 지어내지 마라. '모집자·설계사·대리점' 관련 값은 넣지 마라.
- insurer 는 실제 보험을 인수하는 보험회사(예: NH농협생명, KB손해보험, 삼성화재)만 넣어라.
  문서 하단·상단의 "담당자: OOO 소속: OOO" 처럼 적힌 GA·대리점·중개법인(굿리치, 호남GA 등)
  이름은 insurer 가 아니다 — 절대 넣지 마라.

계약자(契約者)와 피보험자(被保險者)가 다르게 적혀 있으면 둘 다 뽑아라.
- policyholder_name: 계약자(보험료 내는 사람) 이름. 문서에 '계약자' 로 표시된 이름.
- insured_name: 피보험자(보장 대상) 이름. 문서에 '피보험자' 로 표시된 이름.
예: "손유진 고객님을 위한 … (피보험자 안우성고객님)" → policyholder_name="손유진", insured_name="안우성".
계약자·피보험자 구분이 없거나 한 사람이면 둘 다 그 이름(또는 null)으로 둬라.

{"insurer": "보험회사명 (예: 한화손해보험)",
 "product_name": "상품 정식명칭",
 "plan_type": "보장 / 저축 / 변액 / 실손 중 하나 또는 null",
 "premium_won": 월납 보험료 숫자만 (콤마·원 제거),
 "payment_cycle": "MONTHLY / YEARLY / null",
 "insured_period": "보험기간 (예: 90세만기, 20년만기)",
 "payment_period": "납입기간 (예: 30년납, 전기납)",
 "issued_date": "발행/작성일 YYYY-MM-DD 또는 null",
 "policyholder_name": "계약자 이름 또는 null",
 "insured_name": "피보험자 이름 또는 null"}

[문서]
"""

_POLICY_FIELDS = (
    "insurer", "product_name", "plan_type", "premium_won", "payment_cycle",
    "insured_period", "payment_period", "issued_date",
    "policyholder_name", "insured_name",
)


def extract_policy_fields(text: str, model: Optional[str] = None) -> dict[str, Any]:
    """보험 문서 텍스트 → {policy: {...}, warnings}. 증권 헤더용 (담보 표는 별도)."""
    model = model or LLM_MODEL
    raw = _call_llm(_POLICY_PROMPT + text.strip(), model)
    p: dict[str, Any] = {k: (raw.get(k) if isinstance(raw, dict) else None) or None for k in _POLICY_FIELDS}
    warnings: list[str] = []

    if p.get("premium_won") is not None:
        digits = "".join(ch for ch in str(p["premium_won"]) if ch.isdigit())
        p["premium_won"] = int(digits) if digits else None

    pc = str(p.get("payment_cycle") or "").upper()
    if "YEAR" in pc or "연" in pc:
        p["payment_cycle"] = "YEARLY"
    elif "MONTH" in pc or "월" in pc:
        p["payment_cycle"] = "MONTHLY"
    else:
        p["payment_cycle"] = p.get("payment_cycle") or None

    d = str(p.get("issued_date") or "")
    m = re.search(r"(20\d{2})\D?(\d{1,2})\D?(\d{1,2})", d)
    p["issued_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

    # 계약자/피보험자 이름: '고객님' 등 접미어와 공백 제거. 비면 None.
    for _nk in ("policyholder_name", "insured_name"):
        nv = str(p.get(_nk) or "").strip()
        nv = re.sub(r"\s*(고객님|고객|님)$", "", nv).strip()
        p[_nk] = nv or None

    if not p.get("insurer") and not p.get("product_name"):
        warnings.append("보험계약 정보를 찾지 못했습니다.")
    return {"policy": p, "warnings": warnings, "model": model}


_POLICIES_LIST_PROMPT = """다음은 보험 보장분석서의 '보유계약리스트'(또는 가입담보 상세) 를 OCR/추출한 텍스트다.
가입한 보험계약을 회사·상품 단위로 묶어 JSON 배열로 출력하라. '실효/해지' 계약은 빼고 정상계약만.
'컨설턴트·설계사·모집자·대리점·지점' 정보는 절대 넣지 마라. 없는 값은 null, 지어내지 마라.

{"policies": [
  {"insurer": "보험회사명(예: 한화생명, 삼성화재)",
   "product_name": "상품명(회사명 제외, 상품 이름만)",
   "premium_won": 월 보험료 숫자만,
   "payment_period": "납입기간(예: 매월납/20년, 종신)",
   "insured_period": "보장기간/만기(예: 110세, 90세, 종신)"}
]}

[보유계약리스트]
"""


def extract_policies_list(text: str, model: Optional[str] = None) -> dict[str, Any]:
    """보장분석서 보유계약리스트 → {policies: [ {...}, ... ], warnings}. 여러 건."""
    model = model or LLM_MODEL
    raw = _call_llm(_POLICIES_LIST_PROMPT + text.strip(), model)
    rows = raw.get("policies") if isinstance(raw, dict) else raw
    out: list[dict] = []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            ins = (r.get("insurer") or "").strip() or None
            prod = (r.get("product_name") or "").strip() or None
            if not ins and not prod:
                continue
            prem = "".join(ch for ch in str(r.get("premium_won") or "") if ch.isdigit())
            pp = (r.get("payment_period") or "").strip() or None
            cycle = None
            if pp:
                if "월" in pp:
                    cycle = "MONTHLY"
                elif "년납" in pp or "연납" in pp:
                    cycle = "YEARLY"
            out.append({
                "insurer": ins,
                "product_name": prod,
                "plan_type": None,
                "premium_won": int(prem) if prem else None,
                "payment_cycle": cycle,
                "payment_period": pp,
                "insured_period": (r.get("insured_period") or "").strip() or None,
                "issued_date": None,
            })
    warnings = [] if out else ["보유계약 목록을 찾지 못했습니다."]
    return {"policies": out, "warnings": warnings, "model": model}
