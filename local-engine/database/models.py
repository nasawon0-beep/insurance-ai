"""
고객 / 보험계약 요청 스키마 (pydantic v2).

PATCH 는 model_dump(exclude_unset=True) 로 "안 보낸 필드"와 "명시적 null"을 구분한다.
py3.9 호환을 위해 `X | None` 대신 Optional 을 쓴다 (FastAPI/pydantic 런타임 평가).
"""
import re
from datetime import date, datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from . import rrn as _rrn

_STATUSES = {"ACTIVE", "LAPSED", "EXPIRED", "CANCELLED"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GENDER_MAP = {
    "m": "M", "f": "F", "male": "M", "female": "F",
    "남": "M", "여": "F", "남자": "M", "여자": "F",
}

_YM_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _check_date(v, *, allow_2digit_birth=False):
    """느슨한 날짜 입력을 YYYY-MM-DD 로 정규화하고 실제 달력일인지 검증한다."""
    if v is None or v == "":
        return v
    s = str(v).strip()
    if not s:
        return ""
    if not re.fullmatch(r"[0-9\-./\s]+", s):
        raise ValueError("날짜는 YYYY-MM-DD 형식의 실제 달력 날짜여야 합니다.")

    parts = [p for p in re.split(r"[\-./\s]+", s) if p]
    if len(parts) == 1 and len(parts[0]) in (6, 8):
        digits = parts[0]
        year_len = len(digits) - 4
        parts = [digits[:year_len], digits[year_len:year_len + 2], digits[year_len + 2:]]
    elif len(parts) == 2 and len(parts[1]) == 4:
        parts = [parts[0], parts[1][:2], parts[1][2:]]

    if (
        len(parts) != 3
        or len(parts[0]) not in (2, 4)
        or not 1 <= len(parts[1]) <= 2
        or not 1 <= len(parts[2]) <= 2
    ):
        raise ValueError("날짜는 YYYY-MM-DD 형식의 실제 달력 날짜여야 합니다.")

    year = int(parts[0])
    if len(parts[0]) == 2:
        year += 2000
        if allow_2digit_birth and year > date.today().year:
            year -= 100
    normalized = f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    try:
        date.fromisoformat(normalized)
    except ValueError:
        raise ValueError("날짜는 YYYY-MM-DD 형식의 실제 달력 날짜여야 합니다.")
    return normalized


def _check_datetime(v):
    """상담 일시를 정렬 가능한 단일 형식으로 정규화한다.

    - 시각이 포함된 값: 구분자를 'T'로 통일하고 `datetime.isoformat()`으로 재출력.
      tz offset 이 있으면 UTC(`+00:00`)로 환산. tz 없는 값(파일명 유래 로컬시각 등)은
      그대로 naive 로 둔다 — 없는 zone 을 UTC 라고 단정하면 실제 시각을 왜곡한다.
    - 날짜만(YYYY-MM-DD) → _check_date 로 정규화.
    정규화하지 않으면 구분자('T' vs 공백)·offset 표기 차이로 TEXT 컬럼의
    `ORDER BY consulted_at DESC` 가 실제 시간순과 어긋난다."""
    if v is None or v == "":
        return v
    s = str(v).strip()
    if not s:
        return ""
    if "T" in s or (" " in s and ":" in s):
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T"))
        except ValueError:
            raise ValueError("상담 일시는 YYYY-MM-DD 또는 ISO 8601 타임스탬프여야 합니다.")
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    return _check_date(s)


def _check_gender(v):
    if v is None or v == "":
        return v
    key = str(v).strip().lower()
    if key in _GENDER_MAP:
        return _GENDER_MAP[key]
    raise ValueError("성별은 M 또는 F(남/여) 여야 합니다.")


def _check_email(v):
    if v is None or v == "":
        return v
    s = str(v).strip()
    if not _EMAIL_RE.match(s):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    return s


def _check_policy_status(v):
    if v is None:
        return v
    v = str(v).strip()
    if v == "" or v not in _STATUSES:
        raise ValueError("계약 상태는 ACTIVE/LAPSED/EXPIRED/CANCELLED 중 하나여야 합니다.")
    return v


def _check_first_registered_ym(v):
    """최초 고객등록 년월. 비우면 서버가 현재 년월로 채운다. 'YYYY-MM' 또는 'YYYY-MM-DD' 만 허용."""
    if v is None or v == "":
        return v
    v = str(v).strip()
    if not _YM_RE.match(v):
        raise ValueError("최초 등록 년월은 YYYY-MM 또는 YYYY-MM-DD 형식이어야 합니다.")
    return v

# 사용자가 직접 지정하는 고객 상태. 계약 추가 시 '가입', 전 계약 해지 시 '해지'로 자동 보정도 된다.
_CUSTOMER_STATUSES = {"가입", "미가입", "가망", "해지"}


def _check_customer_status(v):
    if v is None or v == "":
        return v
    if v not in _CUSTOMER_STATUSES:
        raise ValueError("customer_status 는 가입/미가입/가망/해지 중 하나여야 합니다.")
    return v


# 계약자(피보험자)와의 관계. 계약의 customer_id 는 피보험자이고,
# policyholder_name 이 비어있으면 본인계약(계약자=피보험자)이다.
_POLICYHOLDER_RELS = {"본인", "배우자", "부", "모", "자녀", "형제자매", "사업자", "기타"}


def _check_policyholder_rel(v):
    if v is None or v == "":
        return v
    if v not in _POLICYHOLDER_RELS:
        raise ValueError(
            "관계는 본인/배우자/부/모/자녀/형제자매/사업자/기타 중 하나여야 합니다."
        )
    return v


class CustomerIn(BaseModel):
    name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    birth_date: Optional[str] = None  # YYYY-MM-DD (검증은 후속)
    gender: Optional[str] = None      # 'M' / 'F' / None
    email: Optional[str] = None
    address: Optional[str] = None
    occupation: Optional[str] = None  # 직업 (예: 주부, 회사원)
    tags: Optional[List[str]] = None  # 태그 목록
    memo: Optional[str] = None
    rrn: Optional[str] = None         # 주민등록번호 (암호화 저장, 응답엔 마스킹만)
    customer_status: Optional[str] = None       # 가망 / 미가입 / 해지 (미지정 시 서버가 '가망')
    birth_date_estimated: Optional[bool] = None  # 생년월일이 추정값인가
    first_registered_ym: Optional[str] = None    # 최초 고객등록 년월 'YYYY-MM' (미입력 시 서버가 현재 년월)

    @field_validator("birth_date")
    @classmethod
    def _valid_birth_date(cls, v):
        return _check_date(v, allow_2digit_birth=True)

    @field_validator("gender")
    @classmethod
    def _valid_gender(cls, v):
        return _check_gender(v)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v):
        return _check_email(v)

    @field_validator("rrn")
    @classmethod
    def _normalize_rrn(cls, v):
        # 형식 불량이면 ValueError -> FastAPI 가 422 로 변환. 저장은 13자리 정규형.
        return _rrn.normalize(v)

    @field_validator("customer_status")
    @classmethod
    def _valid_status(cls, v):
        return _check_customer_status(v)

    @field_validator("first_registered_ym")
    @classmethod
    def _valid_frym(cls, v):
        return _check_first_registered_ym(v)


class CustomerPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    phone: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    occupation: Optional[str] = None
    tags: Optional[List[str]] = None
    memo: Optional[str] = None
    rrn: Optional[str] = None
    customer_status: Optional[str] = None
    birth_date_estimated: Optional[bool] = None
    first_registered_ym: Optional[str] = None

    @field_validator("birth_date")
    @classmethod
    def _valid_birth_date(cls, v):
        return _check_date(v, allow_2digit_birth=True)

    @field_validator("gender")
    @classmethod
    def _valid_gender(cls, v):
        return _check_gender(v)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v):
        return _check_email(v)

    @field_validator("rrn")
    @classmethod
    def _normalize_rrn(cls, v):
        return _rrn.normalize(v)

    @field_validator("customer_status")
    @classmethod
    def _valid_status(cls, v):
        return _check_customer_status(v)

    @field_validator("first_registered_ym")
    @classmethod
    def _valid_frym(cls, v):
        return _check_first_registered_ym(v)


class PolicyIn(BaseModel):
    insurer: Optional[str] = None
    product_name: Optional[str] = None
    policy_number: Optional[str] = None
    plan_type: Optional[str] = None       # 보장 / 저축 / 변액 등 (자유 입력)
    premium: Optional[int] = Field(None, ge=0)  # 원 단위
    payment_cycle: Optional[str] = None   # 'MONTHLY' / 'YEARLY' 등
    start_date: Optional[str] = None
    end_date: Optional[str] = None        # 만기 (명시하면 자동계산 안 함)
    status: str = "ACTIVE"
    memo: Optional[str] = None
    document_id: Optional[str] = None     # 연결된 약관 PDF의 RAG doc_id
    insured_period: Optional[str] = None  # 보험기간 원문 (예: '100세만기', '20년')
    payment_period: Optional[str] = None  # 납입기간 원문 (예: '20년납', '전기납')
    is_own: Optional[bool] = None         # "내가 가입시킴" 플래그. 미지정 시 서버 기본 True.
    # 계약자(피보험자와 다를 때). 이 계약의 customer_id 는 피보험자다.
    # policyholder_name 이 비었으면 본인계약(계약자 = 피보험자), 값이 있으면 그 사람이 계약자.
    policyholder_name: Optional[str] = None  # 계약자 이름
    policyholder_rel: Optional[str] = None   # 계약자와 피보험자의 관계
    # payment_end_date / end_date_derived 는 서버가 파생 — 입력받지 않음

    @field_validator("start_date", "end_date")
    @classmethod
    def _valid_date(cls, v):
        return _check_date(v)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v):
        return _check_policy_status(v)

    @field_validator("policyholder_rel")
    @classmethod
    def _valid_ph_rel(cls, v):
        return _check_policyholder_rel(v)


class PolicyPatch(BaseModel):
    insurer: Optional[str] = None
    product_name: Optional[str] = None
    policy_number: Optional[str] = None
    plan_type: Optional[str] = None
    premium: Optional[int] = Field(None, ge=0)
    payment_cycle: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None        # "" 로 보내면 만기 없음으로 직접 확정
    status: Optional[str] = None
    memo: Optional[str] = None
    document_id: Optional[str] = None
    insured_period: Optional[str] = None
    payment_period: Optional[str] = None
    is_own: Optional[bool] = None  # 보내면 갱신, 명시적 null 이면 '미지정'으로 되돌림
    policyholder_name: Optional[str] = None  # "" 로 보내면 본인계약으로 되돌림
    policyholder_rel: Optional[str] = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _valid_date(cls, v):
        return _check_date(v)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v):
        return _check_policy_status(v)

    @field_validator("policyholder_rel")
    @classmethod
    def _valid_ph_rel(cls, v):
        return _check_policyholder_rel(v)


class IntakeIn(BaseModel):
    text: str = Field(..., min_length=1)  # 상담자가 붙여넣은 자유 텍스트


class AssistantTurn(BaseModel):
    role: str      # "user" / "assistant"
    content: str


class AssistantAsk(BaseModel):
    question: str = Field(..., min_length=1)
    history: Optional[List[AssistantTurn]] = None  # 프론트 세션의 최근 대화 (서버 무상태)
    customer_id: Optional[str] = None  # 특정 고객 컨텍스트로 질문할 때 (고객 상세 탭 등)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v):
        if not str(v).strip():
            raise ValueError("질문을 입력하세요.")
        return v


class ConsultationIn(BaseModel):
    consulted_at: Optional[str] = None  # 비우면 서버가 현재 시각(UTC)으로 채움
    channel: Optional[str] = None       # 방문 / 전화 / 온라인 등 (자유 입력)
    title: Optional[str] = None
    content: Optional[str] = None
    transcript: Optional[str] = None    # 녹취 전사 원문 (있으면). 암호화 저장
    follow_up_at: Optional[str] = None  # 다음 연락 예정일 (YYYY-MM-DD)
    coverage_json: Optional[str] = None  # 보장분석서 보장현황 표 (JSON 문자열, 암호화)

    @field_validator("follow_up_at")
    @classmethod
    def _valid_follow_up_at(cls, v):
        return _check_date(v)

    @field_validator("consulted_at")
    @classmethod
    def _valid_consulted_at(cls, v):
        return _check_datetime(v)


class ConsultationPatch(BaseModel):
    consulted_at: Optional[str] = None
    channel: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    transcript: Optional[str] = None
    follow_up_at: Optional[str] = None
    coverage_json: Optional[str] = None
    follow_up_done_at: Optional[str] = None  # 완료 스탬프 직접 수정용 (평문)

    @field_validator("follow_up_at", "follow_up_done_at")
    @classmethod
    def _valid_follow_up_date(cls, v):
        return _check_date(v)

    @field_validator("consulted_at")
    @classmethod
    def _valid_consulted_at(cls, v):
        return _check_datetime(v)
