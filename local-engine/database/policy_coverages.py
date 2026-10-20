"""계약별 담보 원장 저장/조회 및 PDF 추출 보조.

보장분석 탭(consultations.coverage_json)과 분리된, 고객 상세 > 가입목록용
계약별 담보 원장(policy_coverages) 전용 모듈이다.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class DocumentType(Enum):
    PROPOSAL = "proposal"  # 가입제안서
    PRODUCT_DESCRIPTION = "product_description"  # 상품설명서/상품요약서(참고)
    COVERAGE_ANALYSIS_FULL = "coverage_analysis_full"  # M보장분석 전체
    COVERAGE_ANALYSIS_DETAIL_ONLY = "coverage_analysis_detail_only"  # 보장별 상세표만
    COVERAGE_ANALYSIS_SHORT = "coverage_analysis_short"  # 간편보장분석
    UNKNOWN_INSURANCE_PDF = "unknown_insurance_pdf"


class PageMarker(Enum):
    # 보장분석서 markers
    COVERAGE_SUMMARY = "보장현황"
    COVERAGE_DETAIL_LIST = "가입담보상세List"
    COVERAGE_BY_PRODUCT = "상품별가입담보상세"
    COVERAGE_BY_CATEGORY = "보장별상세표"
    CONTRACT_LEDGER = "보유계약리스트"

    # 가입제안서 markers
    PROPOSAL_INFO = "가입사항"
    PROPOSAL_PREMIUM = "보험료"
    PROPOSAL_RIDERS = "특약명"
    PROPOSAL_COVERAGE = "가입금액"


def classify_document_type(pdf_path: str) -> tuple[DocumentType, dict]:
    """
    PDF 문서 유형 분류 및 메타데이터 추출

    Returns:
        (DocumentType, metadata dict)
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        # 가입담보 표가 후반부에 있는 실제 양식을 놓치지 않도록 전체 텍스트를 분류에 쓴다.
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
        head_text = "\n".join(page_texts[:3])
        all_text = "\n".join(page_texts)

        metadata = {
            "page_count": page_count,
            "head_text_length": len(head_text),
        }

        # 가입제안서 키워드
        proposal_keywords = [
            "가입제안서", "가입설계서", "가입안내서", "보험료납입설계서",
            "계약전 알릴의무", "청약", "납입방법"
        ]
        if any(kw in all_text for kw in proposal_keywords) or "가입사항" in all_text:
            # 가입사항 표가 있는지 확인
            if "가입사항" in all_text and "가입금액" in all_text and "보험료" in all_text:
                metadata["has_proposal_table"] = True
                return DocumentType.PROPOSAL, metadata

        # 보장분석서 키워드
        coverage_analysis_keywords = [
            "보장분석", "보장현황", "간편보장분석",
            "보유계약", "가입담보상세"
        ]
        if any(kw in all_text for kw in coverage_analysis_keywords):
            # 긴 양식 vs 짧은 양식 구분
            if page_count <= 10:
                metadata["is_short_form"] = True
                return DocumentType.COVERAGE_ANALYSIS_SHORT, metadata
            elif "가입담보상세List" in all_text or "상품별가입담보상세" in all_text:
                metadata["is_full_form"] = True
                return DocumentType.COVERAGE_ANALYSIS_FULL, metadata
            elif "보장별상세표" in all_text:
                metadata["is_detail_only"] = True
                return DocumentType.COVERAGE_ANALYSIS_DETAIL_ONLY, metadata
            else:
                # 기본값: 전체 양식
                return DocumentType.COVERAGE_ANALYSIS_FULL, metadata

        if any(marker in all_text for marker in ("상품설명서", "상품요약서", "보험상품")) and any(
            marker in all_text for marker in ("보장내용", "가입금액", "보험료", "보험기간")
        ):
            metadata["reference_only"] = True
            return DocumentType.PRODUCT_DESCRIPTION, metadata

        return DocumentType.UNKNOWN_INSURANCE_PDF, metadata


def detect_candidate_pages(pdf_path: str, doc_type: DocumentType) -> dict:
    """
    문서 유형별 후보 페이지 탐지

    Returns:
        {
            "coverage_summary_pages": [1, 2],
            "detail_list_pages": [5, 6, 7],
            "product_detail_pages": [10, 11],
            "category_detail_pages": [15, 16],
            "contract_ledger_pages": [3, 4],
            "proposal_table_pages": [2, 3],
        }
    """
    import pdfplumber

    result = {
        "coverage_summary_pages": [],
        "detail_list_pages": [],
        "product_detail_pages": [],
        "category_detail_pages": [],
        "contract_ledger_pages": [],
        "proposal_table_pages": [],
    }

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_num = i + 1
            text = page.extract_text() or ""

            # 보장현황 (간편보장분석 2페이지)
            if "보장현황" in text and ("미가입" in text or "부족" in text or "충분" in text):
                result["coverage_summary_pages"].append(page_num)

            # 가입담보상세List
            if "가입담보상세List" in text or "가입담보 상세 List" in text or "NO 보험사" in text or "NO 구분 회사" in text:
                result["detail_list_pages"].append(page_num)

            # 상품별가입담보상세
            if "상품별가입담보상세" in text:
                result["product_detail_pages"].append(page_num)

            # 보장별상세표
            if "보장별상세표" in text or ("보장군" in text and "권장금액" in text) or "전체 담보 진단 현황" in text:
                result["category_detail_pages"].append(page_num)

            # 보유계약리스트
            if "보유계약" in text and "월보험료" in text:
                result["contract_ledger_pages"].append(page_num)

            # 가입사항 (가입제안서)
            if ("가입금액" in text and "보험료" in text) or (
                doc_type == DocumentType.PRODUCT_DESCRIPTION and any(k in text for k in ("보장내용", "보험기간", "가입금액"))
            ):
                result["proposal_table_pages"].append(page_num)

    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


_AMOUNT_RE = re.compile(r"(?P<amount>\d[\d,]*(?:\.\d+)?\s*(?:억|천|백|십)?\s*만?|\d[\d,]*\s*원)(?:\s|$)")
_DATE_RE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}")
_AMOUNT_TOKEN_RE = r"\d[\d,]*(?:\.\d+)?\s*(?:억(?:\s*\d[\d,]*(?:\.\d+)?\s*만(?:\s*\d[\d,]*(?:\.\d+)?\s*천)?)?|천\s*만|백\s*만|십\s*만|만(?:\s*\d[\d,]*(?:\.\d+)?\s*천)?|천|백|십|원)?"
_ROW_RE = re.compile(
    rf"^(?P<no>\d{{1,3}})\s+(?P<ctype>실손|정액|기타)\s+"
    rf"(?P<body>.+?)\s+(?P<amount>{_AMOUNT_TOKEN_RE})$"
)
_DETAIL_ROW_RE = re.compile(
    r"^(?:(?P<std>[가-힣A-Za-z0-9%·/()\-+]+)\s+)?"
    r"(?P<insurer>삼성화재|메리츠화재|메리츠|한화생명|한화손보|현대해상|KB손보|DB손보|ABL생명|흥국화재|하나손보|MG손보|라이나생명|신한라이프생명|흥국생명)\s+"
    rf"(?P<rest>.+?)\s+(?P<amount>{_AMOUNT_TOKEN_RE})\s+"
    r"(?P<start>\d{4}[-.]\d{2}[-.]\d{2})\s+(?P<end>\d{4}[-.]\d{2}[-.]\d{2})$"
)
_KNOWN_INSURERS = (
    "삼성화재", "메리츠화재", "메리츠", "한화생명", "한화손보", "현대해상", "KB손보",
    "DB손보", "ABL생명", "흥국화재", "하나손보", "MG손보", "라이나생명",
    "신한라이프생명", "흥국생명", "교보생명", "삼성생명", "NH농협생명",
)

_SHORT_FORM_STANDARD_NAMES = {
    "상해사망": "상해사망",
    "질병사망": "질병사망",
    "상해80%미만후유장해": "상해 3%이상 후유장해",
    "질병80%미만후유장해": "질병 3%이상 후유장해",
    "장기요양간병비": "질병 간병인사용 입원일당",
    "경증치매진단": "경증치매진단",
    "간병인/간호간병상해일당": "상해 간병인사용 입원일당",
    "간병인/간호간병질병일당": "질병 간병인사용 입원일당",
    "일반암": "일반암 진단비",
    "유사암": "유사암 진단비",
    "고액암": "특정암 진단비",
    "고액(표적)항암치료비": "고액항암 치료비(표적)",
    "뇌혈관질환": "뇌혈관 진단비",
    "뇌졸중": "뇌졸중 진단비",
    "뇌출혈": "뇌출혈 진단비",
    "허혈성심장질환": "허혈성심장질환 진단비",
    "급성심근경색증": "급성심근경색증 진단비",
    "상해입원의료비": "상해입원",
    "상해통원의료비": "상해통원",
    "질병입원의료비": "질병입원",
    "질병통원의료비": "질병통원",
    "3대비급여실손": "1인실 입원일당",
    "상해수술비": "상해수술비",
    "질병수술비": "질병수술비",
    "암수술비": "암수술비",
    "뇌혈관질환수술비": "뇌혈관수술비",
    "허혈성심장질환수술비": "허혈심장질환수술비",
    "상해입원일당": "상해일당",
    "질병입원일당": "질병일당",
    "벌금(대인/스쿨존/대물)": "벌금",
    "교통사고처리지원금": "교통사고처리 지원금",
    "변호사선임비용": "변호사 선임비용",
    "자동차사고부상": "자동차사고 부상치료비",
    "골절진단비": "골절 진단비",
    "보철치료비": "치아보철 치료비",
    "가족/일상/자녀배상": "일상생활 배상책임",
    "화재벌금": "화재벌금",
}
_SHORT_FORM_ROW_RE = re.compile(r"^(?P<name>[가-힣A-Za-z0-9·%()/]+)\s+(?P<rest>.+)$")
_DIAGNOSIS_DETAIL_STATUSES = {"충분", "부족", "미가입"}
_INTEGRATED_INSURER = "통합보장분석"
_INTEGRATED_PRODUCT = "M·보장분석"

_STD_ALIASES: list[tuple[str, str]] = [
    ("질병입원의료", "질병입원"), ("상해입원의료", "상해입원"),
    ("질병외래의료비", "질병통원"), ("질병통원의료비", "질병통원"), ("질병통원", "질병통원"),
    ("상해외래의료비", "상해통원"), ("상해통원의료비", "상해통원"), ("상해통원", "상해통원"),
    ("일상생활배상", "일상생활 배상책임"), ("가족생활배상", "일상생활 배상책임"),
    ("질병1~5종수술비", "질병종수술"), ("질병1-5종수술비", "질병종수술"), ("질병N종수술비", "질병종수술"), ("질병종수술", "질병종수술"),
    ("상해1~5종수술비", "상해종수술"), ("상해1-5종수술비", "상해종수술"), ("상해N종수술비", "상해종수술"), ("상해종수술", "상해종수술"),
    ("뇌혈관질환진단비", "뇌혈관 진단비"), ("뇌혈관질환진단", "뇌혈관 진단비"), ("뇌혈관진단비", "뇌혈관 진단비"),
    ("뇌출혈진단비", "뇌출혈 진단비"), ("두번째뇌출혈", "뇌출혈 진단비"), ("뇌졸중진단비", "뇌졸중 진단비"),
    ("허혈성심장질환진단비", "허혈성심장질환 진단비"), ("허혈성심장질환진단", "허혈성심장질환 진단비"), ("허혈심장질환진단", "허혈성심장질환 진단비"),
    ("유사암진단비", "유사암 진단비"), ("유사암진단", "유사암 진단비"), ("기타피부암", "유사암 진단비"), ("갑상선암", "유사암 진단비"), ("제자리암", "유사암 진단비"), ("경계성종양", "유사암 진단비"),
    ("통합암진단비(유사암제외)", "통합암 진단비"), ("통합암진단비", "통합암 진단비"), ("통합암", "통합암 진단비"),
    ("전이암진단비", "특정암 진단비"), ("림프절전이암진단비", "특정암 진단비"), ("특정전이암진단비", "특정암 진단비"), ("10대특정암", "특정암 진단비"), ("15대특정암", "특정암 진단비"), ("4대고액암", "특정암 진단비"), ("특정암진단", "특정암 진단비"),
    ("암입원", "암입원일당"), ("암간병인사용입원", "질병 간병인사용 입원일당"), ("간병인질병입원일당", "질병 간병인사용 입원일당"),
    ("간병인상해입원일당", "상해 간병인사용 입원일당"), ("질병간호·간병통합서비스사용일당", "질병 간병인지원 입원일당"),
    ("상해간호·간병통합서비스사용일당", "상해 간병인지원 입원일당"), ("질병입원일당", "질병일당"), ("상해입원일당", "상해일당"),
    ("질병중환자실입원일당", "질병일당"), ("상해중환자실입원일당", "상해일당"), ("상급종합병원질병입원일당", "질병일당"),
    ("상해사망(유병자)", "상해사망"), ("상해사망", "상해사망"), ("질병사망", "질병사망"),
    ("상해후유80%", "상해 80%이상 후유장해"), ("상해80%이상후유장해", "상해 80%이상 후유장해"), ("상해후유장해", "상해 3%이상 후유장해"),
    ("질병80%이상후유장해", "질병 80%이상 후유장해"), ("질병후유장해", "질병 3%이상 후유장해"),
    ("뇌혈관질환수술", "뇌혈관수술비"), ("허혈성심장질환수술", "허혈심장질환수술비"), ("급성심근경색진단", "급성심근경색증 진단비"),
    ("상해수술(유병자)", "상해수술비"), ("질병수술(유병자)", "질병수술비"), ("특정질병수술", "질병수술비"), ("기타수술", "질병수술비"),
    ("질병수술비", "질병수술비"), ("상해수술비", "상해수술비"),
    ("교통사고처리지원금", "교통사고처리 지원금"), ("교통사고벌금", "벌금"), ("과실치사상벌금", "벌금"),
    ("자동차사고부상", "자동차사고 부상치료비"), ("골절진단비", "골절 진단비"),
    ("질병입원간병인사용일당", "질병 간병인사용 입원일당"), ("1인실입원일당", "1인실 입원일당"), ("암수술비", "암수술비"),
    ("질병입원일당", "질병일당"), ("상해입원일당", "상해일당"),
    ("치매진단(경증)", "경증치매진단"), ("골절통원치료", "골절 진단비"), ("중증화상", "화상 진단비"),
]


def compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def normalize_standard_name(company_name: str | None, standard_name: str | None = None) -> str:
    # PDF의 신정원 담보명은 문서 작성자가 확정한 분류다. 회사 담보명의
    # 광범위 키워드로 이를 덮어쓰면 의미가 다른 담보가 합산된다.
    explicit = (standard_name or "").strip()
    if explicit:
        if compact(explicit) == "기타":
            return "기타"
        explicit_hay = compact(explicit)
        exact_source_names = {
            "허혈성심장질환수술비": "허혈심장질환수술비",
            "허혈심장질환수술비": "허혈심장질환수술비",
            "질병간병인사용입원일당": "질병 간병인사용 입원일당",
            "상해간병인사용입원일당": "상해 간병인사용 입원일당",
            "변호사선임비용": "변호사 선임비용",
        }
        if explicit_hay in exact_source_names:
            return exact_source_names[explicit_hay]
        if "외상성뇌출혈" in explicit_hay or "상해성뇌출혈" in explicit_hay:
            return explicit
        for needle, canonical in _STD_ALIASES:
            if compact(needle) in explicit_hay:
                return canonical
        return explicit

    hay = compact(company_name or "")
    if "외상성뇌출혈" in hay or "상해성뇌출혈" in hay:
        return "기타"
    for needle, canonical in _STD_ALIASES:
        if compact(needle) in hay:
            return canonical
    if "입원일당" in hay:
        if "상해" in hay:
            return "상해일당"
        if "질병" in hay:
            return "질병일당"
    return "기타"


def parse_amount(amount_text: str | None) -> Optional[int]:
    s = (amount_text or "").replace(",", "").replace("원", "").strip()
    if not s:
        return None
    total = 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*억", s)
    if m:
        total += int(float(m.group(1)) * 100_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*천\s*만", s)
    if m:
        total += int(float(m.group(1)) * 10_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*백\s*만", s)
    if m:
        total += int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*십\s*만", s)
    if m:
        total += int(float(m.group(1)) * 100_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*만", s)
    if m and "천만" not in s and "백만" not in s and "십만" not in s:
        total += int(float(m.group(1)) * 10_000)
        tail = re.search(r"만\s*(\d+(?:\.\d+)?)\s*천", s)
        if tail:
            total += int(float(tail.group(1)) * 1_000)
    if total == 0:
        # 원문 표에서 "1천"은 1천만원 약식으로 사용된다.
        standalone = re.fullmatch(r"(\d+(?:\.\d+)?)\s*천", s)
        if standalone:
            total += int(float(standalone.group(1)) * 10_000_000)
    if total:
        return total
    m = re.search(r"\d+(?:\.\d+)?", s)
    return int(float(m.group(0))) if m else None


def _split_company_standard(body: str) -> tuple[str, str]:
    compact_body = compact(body)
    known_std = [
        "뇌혈관질환진단", "허혈성심장질환진단", "질병종수술", "상해종수술", "유사암진단",
        "통합암", "특정암진단", "암진단", "상해사망", "질병사망", "뇌졸중수술", "급성심근경색수술",
        "상해수술", "암수술", "골절진단", "화상진단", "기타",
    ]
    for std_token in sorted(known_std, key=len, reverse=True):
        if compact_body.endswith(compact(std_token)):
            # 원문 공백 보존을 위해 뒤에서 토큰 첫 글자 위치를 대략 찾는다.
            idx = body.rfind(std_token)
            if idx < 0:
                idx = body.rfind(std_token[:2])
            if idx > 0:
                return body[:idx].strip(), body[idx:].strip()
    tokens = body.split()
    if len(tokens) <= 1:
        return body.strip(), "기타"
    best_i = len(tokens) - 1
    best_score = -1
    for i in range(1, len(tokens)):
        std = " ".join(tokens[i:])
        score = 0
        for needle, _canonical in _STD_ALIASES:
            if compact(needle) in compact(std):
                score += 4
        if compact(std) in {"기타", "암진단", "상해사망", "질병사망", "뇌졸중수술", "급성심근경색수술", "상해수술", "암수술"}:
            score += 2
        if score > best_score:
            best_score, best_i = score, i
    return " ".join(tokens[:best_i]).strip(), " ".join(tokens[best_i:]).strip() or "기타"


def _policy_from_appendix_page(lines: list[str]) -> dict:
    insurer = product = start = end = premium = None
    for i, line in enumerate(lines):
        if "님의 상품별 가입담보상세" in line or "상품별 가입담보상세" in line or re.fullmatch(r"20\d{2}[-.]\d{2}[-.]\d{2}.*", line):
            continue
        # 추출기/문서 레이아웃에 따라 보험사/상품명이 같은 줄로 붙어 나올 수 있다.
        if not insurer and line in _KNOWN_INSURERS:
            insurer = line
            if i + 1 < len(lines) and "계약자" not in lines[i + 1] and "가입담보" not in lines[i + 1]:
                product = lines[i + 1].strip()
        if not insurer:
            for known in sorted(_KNOWN_INSURERS, key=len, reverse=True):
                if line.startswith(known) and len(line) > len(known):
                    insurer = known
                    product = line[len(known):].strip() or product
                    break
        if not insurer and i == 1 and "상품별보험가입현황" not in compact(line) and "상품별가입담보상세" not in compact(line) and line.strip() not in {"가입담보명 및 가입금액 (단위 : 원)", "NO 구분 회사 담보명 신정원 담보명 가입금액"}:
            insurer = line.strip()
        if insurer and not product and i == 2 and "NO 구분" not in line:
            product = line.strip()
        if "보험기간" in line:
            dates = _DATE_RE.findall(line)
            if len(dates) >= 2:
                start, end = dates[0].replace(".", "-"), dates[1].replace(".", "-")
            pm = re.search(r"월\s*보험료\s*([^\s]+)", line)
            if pm:
                premium = pm.group(1)
    return {"insurer": insurer, "product_name": product, "start_date": start, "end_date": end, "premium_text": premium}


def _parse_appendix_pages(pages: list[tuple[int, str]], source_file_name: str | None, source_hash: str | None) -> list[dict]:
    rows: list[dict] = []
    current_policy: dict = {}
    for page_no, text in pages:
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        compact_text = compact(text)
        if "별첨상품별보험가입현황" in compact_text or "상품별가입담보상세" in compact_text:
            pol = _policy_from_appendix_page(lines)
            if pol.get("insurer") or pol.get("product_name"):
                current_policy = pol
        if "가입담보명및가입금액" not in compact_text and "상품별가입담보상세" not in compact_text:
            continue
        for line in lines:
            m = _ROW_RE.match(line)
            if not m:
                continue
            company, std = _split_company_standard(m.group("body"))
            if not company or company == "NO":
                continue
            rows.append({
                "source_file_name": source_file_name,
                "source_hash": source_hash,
                "source_page": page_no,
                "insurer": current_policy.get("insurer"),
                "product_name": current_policy.get("product_name"),
                "rider_no": m.group("no"),
                "coverage_type": m.group("ctype"),
                "rider_name": company,
                "rider_name_raw": company,
                "standard_name_raw": std,
                "standard_name": normalize_standard_name(company, std),
                "source_standard_name": None,
                "source_type": "contract_ledger",
                "source_link_status": "unlinked",
                "review_status": "unreviewed",
                "amount": parse_amount(m.group("amount")),
                "amount_text": m.group("amount"),
                "start_date": current_policy.get("start_date"),
                "end_date": current_policy.get("end_date"),
                "confidence": 0.94,
                "raw_text": line,
            })
    return rows


def _parse_detail_list_pages(pages: list[tuple[int, str]], source_file_name: str | None, source_hash: str | None) -> list[dict]:
    rows: list[dict] = []
    for page_no, text in pages:
        if "가입담보 상세 List" not in text and "담보별 가입 현황" not in text:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for line in lines:
            m = _DETAIL_ROW_RE.match(line)
            if not m:
                continue
            rest = m.group("rest").strip()
            # 상품명/회사담보명 경계는 원문 PDF 공백이 불안정하므로 담보 키워드 시작점을 우선 사용한다.
            keys = ["[건강]", "[간편]", "갱신형", "유사암", "통합암", "전이암", "뇌혈관", "허혈", "질병", "상해", "암", "가족생활", "교통사고", "자동차사고", "민사소송", "화상", "골절"]
            cut = min([idx for k in keys if (idx := rest.find(k, 8)) >= 0] or [max(0, len(rest) // 2)])
            product = rest[:cut].strip() or None
            rider = rest[cut:].strip() or rest
            std = m.group("std") or None
            rows.append({
                "source_file_name": source_file_name,
                "source_hash": source_hash,
                "source_page": page_no,
                "insurer": m.group("insurer"),
                "product_name": product,
                "coverage_type": None,
                "rider_name": rider,
                "rider_name_raw": rider,
                "standard_name_raw": std,
                "standard_name": normalize_standard_name(rider, std),
                "source_standard_name": normalize_standard_name(None, std) if std else None,
                "source_type": "detail_list",
                "source_link_status": "source_exact" if std else "unlinked",
                "review_status": "unreviewed",
                "amount": parse_amount(m.group("amount")),
                "amount_text": m.group("amount"),
                "start_date": m.group("start").replace(".", "-"),
                "end_date": m.group("end").replace(".", "-"),
                "confidence": 0.82,
                "raw_text": line,
            })
    return rows


def _parse_short_form_summary_pages(pages: list[tuple[int, str]], source_file_name: str | None, source_hash: str | None) -> list[dict]:
    """7p 짧은 보장분석서의 '전체 보장현황' 표(37개 기준담보)를 담보 원장으로 변환한다."""
    rows: list[dict] = []
    seen: set[str] = set()
    for page_no, text in pages:
        if "전체 보장현황" not in text and "기준담보/권장금액" not in text:
            continue
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line in {"사망", "장해", "치매", "간병", "암 진단", "뇌/심장", "진단", "실손", "의료비", "수술", "입원", "운전자", "기타"}:
                continue
            m = _SHORT_FORM_ROW_RE.match(line)
            if not m:
                continue
            rider = m.group("name").strip()
            std = _SHORT_FORM_STANDARD_NAMES.get(rider)
            if not std or rider in seen:
                continue
            tokens = m.group("rest").split()
            if not tokens:
                continue
            amount_parts: list[str] = []
            for tok in tokens:
                if tok == "-":
                    if not amount_parts:
                        amount_parts = ["-"]
                    break
                if re.fullmatch(r"[\d,]+(?:억|만|원)?", tok):
                    amount_parts.append(tok)
                    continue
                break
            if amount_parts == ["-"] or not amount_parts:
                amount_text = "-"
                amount = None
            else:
                amount_text = " ".join(amount_parts)
                amount = parse_amount(amount_text) or 0
            seen.add(rider)
            rows.append({
                "source_file_name": source_file_name,
                "source_hash": source_hash,
                "source_page": page_no,
                "insurer": "알 수 없음",
                "product_name": "보장분석 요약",
                "coverage_type": None,
                "rider_name": rider,
                "standard_name": std,
                "amount": amount,
                "amount_text": amount_text,
                "amount_status": "unknown_dash" if amount is None else ("explicit_zero" if amount == 0 else "known"),
                "confidence": 0.9,
                "raw_text": line,
            })
    return rows


def _parse_diagnosis_detail_pages(pages: list[tuple[int, str]], source_file_name: str | None, source_hash: str | None) -> list[dict]:
    """한 줄로 추출된 '보장군 보장상세 권장금액 가입금액' 표를 담보 원장으로 변환한다."""
    rows: list[dict] = []
    seen: set[str] = set()
    amount_value_re = re.compile(r"-?(?:\d[\d,]*(?:\.\d+)?(?:억\d[\d,]*만|억|천만|백만|십만|만|원)?|0)")
    for page_no, text in pages:
        if "보장군보장상세권장금액가입금액" not in compact(text):
            continue
        in_table = False
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if "보장군" in line and "보장상세" in line and "권장금액" in line and "가입금액" in line:
                in_table = True
                continue
            if not in_table:
                continue
            if line.startswith("*") or "GA2-" in line or "담보별" in line:
                break
            tokens = line.split()
            if len(tokens) < 5 or tokens[-1] not in _DIAGNOSIS_DETAIL_STATUSES:
                continue
            shortage = tokens[-2]
            amount_text = tokens[-3]
            recommended_text = tokens[-4]
            rider = " ".join(tokens[:-4]).strip()
            if not rider or rider in {"보장군", "보장상세"}:
                continue
            # 표의 숫자 컬럼 3개(권장/가입/부족)가 모두 금액 형태일 때만 채택해 하단 범례 텍스트를 제외한다.
            if not all(amount_value_re.fullmatch(v) for v in (recommended_text, amount_text, shortage)):
                continue
            key = f"{page_no}:{compact(rider)}:{amount_text}"
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "source_file_name": source_file_name,
                "source_hash": source_hash,
                "source_page": page_no,
                # M·보장분석 PDF의 보장별 상세 내역은 개별 보험사/상품이 없는
                # 통합 담보 현황이다. UI 검수 화면에서 "알 수 없음 / 보장분석 상세"로
                # 보이지 않도록 통합 분석 문서임을 명확히 표시한다.
                "insurer": _INTEGRATED_INSURER,
                "product_name": _INTEGRATED_PRODUCT,
                "coverage_type": None,
                "rider_name": rider,
                "standard_name": normalize_standard_name(rider, rider),
                "amount": parse_amount(amount_text) or 0,
                "amount_text": amount_text,
                "confidence": 0.88,
                "raw_text": line,
            })
    return rows


_AMOUNT_STATUSES = {"known", "explicit_zero", "unknown_dash", "unknown_blank", "parse_failed"}


def infer_amount_status(amount, amount_text, supplied=None) -> str:
    supplied = str(supplied or "").strip()
    if supplied in _AMOUNT_STATUSES:
        return supplied
    text = str(amount_text or "").strip()
    if text == "-":
        return "unknown_dash"
    if not text and amount is None:
        return "unknown_blank"
    if amount == 0 and re.sub(r"\s+", "", text) in {"0", "0원"}:
        return "explicit_zero"
    if amount is None:
        return "parse_failed"
    return "known"


def _with_compat_fields(row: dict) -> dict:
    """프론트 보장분석 UI가 기대하는 필드명을 기존 원장 필드와 함께 제공한다."""
    coverage_name = row.get("coverage_name") or row.get("rider_name") or row.get("standard_name")
    amount_status = infer_amount_status(row.get("amount"), row.get("amount_text"), row.get("amount_status"))
    current_amount = row.get("current_amount") if "current_amount" in row else (
        None if amount_status in {"unknown_dash", "unknown_blank", "parse_failed"} else row.get("amount_text")
    )
    return {**row, "coverage_name": coverage_name, "current_amount": current_amount, "amount_status": amount_status}


def _cell_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _appendix_header_policy(rows: list[list[str]], header_index: int) -> tuple[str, dict]:
    """Extract insurer/product from appendix table geometry, without an allow-list."""
    title_index = next(
        (index for index, row in enumerate(rows[:header_index])
         if any("상품별보험가입현황" in compact(cell) for cell in row)),
        None,
    )
    if title_index is None:
        return "ambiguous", {}
    stop_index = next(
        (index for index, row in enumerate(rows[title_index + 1:header_index], title_index + 1)
         if any(marker in compact(cell) for cell in row
                for marker in ("계약자/피보험자", "가입담보명및가입금액", "보험기간"))),
        header_index,
    )
    identity_rows = [
        [_cell_text(cell) for cell in row if _cell_text(cell)]
        for row in rows[title_index + 1:stop_index]
        if any(_cell_text(cell) for cell in row)
    ]
    if not identity_rows:
        return "continuation", {}
    if len(identity_rows) != 2 or any(len(row) != 1 for row in identity_rows):
        return "ambiguous", {}
    insurer, product_name = identity_rows[0][0], identity_rows[1][0]
    if not insurer or not product_name or _DATE_RE.search(insurer) or _DATE_RE.search(product_name):
        return "ambiguous", {}
    dates = _DATE_RE.findall(" ".join(cell for row in rows[:header_index] for cell in row if cell))
    return "explicit", {
        "insurer": insurer,
        "product_name": product_name,
        "start_date": dates[0].replace(".", "-") if dates else None,
        "end_date": dates[1].replace(".", "-") if len(dates) > 1 else None,
    }


def _rider_number(value) -> int | None:
    match = re.fullmatch(r"\d+", _cell_text(value))
    return int(match.group(0)) if match else None


def _can_carry_appendix_identity(
    previous_page: int | None,
    current_page: int,
    previous_rider_no: int | None,
    first_rider_no: int | None,
) -> bool:
    return (
        previous_page is not None
        and current_page == previous_page + 1
        and previous_rider_no is not None
        and first_rider_no is not None
        and first_rider_no == previous_rider_no + 1
        and first_rider_no > 1
    )


def _find_cell_index(row: list[str], token: str) -> int | None:
    return next((index for index, cell in enumerate(row) if token in cell), None)


def _same_source_rider(left: str | None, right: str | None) -> bool:
    a, b = compact(left), compact(right)
    return a == b or (min(len(a), len(b)) >= 12 and (a.startswith(b) or b.startswith(a)))


def _known_source_identity(value: str | None) -> bool:
    return compact(value).lower() not in {"", "-", "미확인", "알수없음", "unknown", "none"}


def _insurer_identity(value: str | None) -> str:
    normalized = compact(value).replace("㈜", "").replace("(주)", "").replace("주식회사", "")
    normalized = normalized.replace("손해보험", "손해").replace("손보", "손해")
    if normalized.endswith("생명보험"):
        normalized = normalized[:-2]
    elif normalized.endswith("보험"):
        normalized = normalized[:-2]
    return normalized.lower()


def _same_source_insurer(left: str | None, right: str | None) -> bool:
    return _insurer_identity(left) == _insurer_identity(right)


def _repair_clipped_appendix_rider(rider: str, product_name: str | None) -> str:
    """표 오른쪽 경계에서 건강가입 꼬리가 잘린 회사 담보명만 복원한다."""
    if "건강" not in str(product_name or "") or "(건강가입)" in rider or "[건강]" in rider:
        return rider
    if not any(marker in rider for marker in ["입원일당", "기계적혈전제거술"]):
        return rider
    marker = rider.rfind("(건강")
    if marker >= 0:
        return rider[:marker] + "(건강가입)"
    if rider.endswith("("):
        return rider[:-1] + "(건강가입)"
    return rider + "(건강가입)"


def _chars_in_bbox(page, bbox) -> list[dict]:
    return [
        char for char in page.chars
        if char["x0"] >= bbox[0] - 1.0 and char["x1"] <= bbox[2] + 0.1
        and char["top"] >= bbox[1] - 0.1 and char["bottom"] <= bbox[3] + 0.1
    ]


def _infer_standard_column_geometry(page, table, rows: list[list[str]], start: int, column: int) -> tuple[float | None, float]:
    """표 전체에서 신정원 열의 반복 시작점/글자 advance 비율을 추정한다."""
    relative_starts: list[float] = []
    scales: list[float] = []
    for row_index in range(start, min(len(rows), len(table.rows))):
        cells = table.rows[row_index].cells
        if column >= len(cells) or not cells[column]:
            continue
        bbox = cells[column]
        words = page.crop(bbox).extract_words(use_text_flow=True, keep_blank_chars=False, x_tolerance=1, y_tolerance=1)
        for word in words:
            relative = float(word["x0"]) - float(bbox[0])
            if 0.75 <= relative <= 6.0:
                relative_starts.append(round(relative, 2))
        extracted = compact(rows[row_index][column] if column < len(rows[row_index]) else "")
        chars = sorted(_chars_in_bbox(page, bbox), key=lambda char: char["x0"])
        if extracted and len(chars) == len(extracted):
            for left, right in zip(chars, chars[1:]):
                adv = float(left.get("adv") or left.get("width") or 0)
                if adv > 0 and right["x0"] > left["x0"]:
                    ratio = (right["x0"] - left["x0"]) / adv
                    if 0.55 <= ratio <= 1.15:
                        scales.append(ratio)
    anchor = Counter(relative_starts).most_common(1)[0][0] if relative_starts else None
    scale = sorted(scales)[len(scales) // 2] if scales else 0.91
    return anchor, scale


def _verified_standard_cell_text(page, table, row_index: int, column: int, anchor_relative: float | None, advance_scale: float) -> tuple[str | None, str]:
    """overflow glyph와 겹친 셀에서 신정원 열의 독립 text stream만 복원한다."""
    if anchor_relative is None or row_index >= len(table.rows):
        return None, "needs_review"
    cells = table.rows[row_index].cells
    if column >= len(cells) or not cells[column]:
        return None, "needs_review"
    bbox = cells[column]
    anchor = float(bbox[0]) + anchor_relative
    words = page.crop(bbox).extract_words(use_text_flow=True, keep_blank_chars=False, x_tolerance=1, y_tolerance=1)
    chars = sorted(_chars_in_bbox(page, bbox), key=lambda char: char["x0"])
    anchor_chars = [char for char in chars if abs(float(char["x0"]) - anchor) <= 0.45]
    standard_advance = float(anchor_chars[0].get("adv") or anchor_chars[0].get("width") or 0) * advance_scale if anchor_chars else 0
    competing_words = [word for word in words if float(word["x0"]) <= float(bbox[0]) + 0.45]
    if standard_advance and any(float(word["x1"]) - float(bbox[0]) > standard_advance * 4.5 for word in competing_words):
        return None, "needs_review"
    anchored_words = [word for word in words if abs(float(word["x0"]) - anchor) <= 0.45]
    if anchored_words:
        text = _cell_text(" ".join(str(word["text"]) for word in sorted(anchored_words, key=lambda word: word["x0"])))
        if text:
            return text, "geometry_word"

    start_candidates = [char for char in chars if abs(float(char["x0"]) - anchor) <= 0.45]
    if not start_candidates:
        return None, "needs_review"
    current = min(start_candidates, key=lambda char: abs(float(char["x0"]) - anchor))
    selected = [current]
    remaining = [char for char in chars if char is not current]
    while remaining:
        adv = float(current.get("adv") or current.get("width") or 0)
        if adv <= 0:
            break
        target = float(current["x0"]) + adv * advance_scale
        tolerance = max(0.55, adv * 0.18)
        candidates = [char for char in remaining if float(char["x0"]) > float(current["x0"]) and abs(float(char["x0"]) - target) <= tolerance]
        if not candidates:
            break
        current = min(candidates, key=lambda char: abs(float(char["x0"]) - target))
        selected.append(current)
        remaining.remove(current)
    text = _cell_text("".join(str(char["text"]) for char in selected))
    return (text, "geometry_trace") if text else (None, "needs_review")


def parse_policy_coverages_from_pdf(
    pdf_path: str,
    source_file_name: str | None = None,
    source_hash: str | None = None,
    *,
    with_occurrences: bool = False,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """pdfplumber 원시 표 셀에서 별첨 원장과 원본 분류 연결을 함께 추출한다."""
    import pdfplumber

    ledger: list[dict] = []
    source_links: list[dict] = []
    source_occurrences: list[dict] = []
    current_policy: dict = {}
    last_appendix_page: int | None = None
    last_appendix_rider_no: int | None = None
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table_index, found_table in enumerate(page.find_tables() or []):
                rows = [[_cell_text(cell) for cell in row] for row in (found_table.extract() or [])]
                if not rows:
                    continue
                compact_table = compact(" ".join(cell for row in rows for cell in row))
                detail_header = next((i for i, row in enumerate(rows) if all(_find_cell_index(row, token) is not None for token in ["구분", "회사명", "담보명"])), None)
                if detail_header is not None:
                    header = rows[detail_header]
                    indices = {name: index for name in ["구분", "회사명", "상품명", "담보명", "보험시기", "보험종기"] if (index := _find_cell_index(header, name)) is not None}
                    amount_index = _find_cell_index(header, "가입금액")
                    current_group = current_insurer = None
                    for source_row, row in enumerate(rows[detail_header + 1:], start=detail_header + 1):
                        if amount_index is None or len(row) <= max(amount_index, indices.get("담보명", 0)):
                            continue
                        group_cell = row[indices["구분"]]
                        if group_cell:
                            current_group = re.sub(r"\s*\([^)]*\)\s*$", "", group_cell).strip()
                        insurer_cell = row[indices["회사명"]]
                        if insurer_cell:
                            current_insurer = insurer_cell
                        rider = row[indices["담보명"]]
                        amount_text = row[amount_index]
                        if current_group and rider:
                            source_links.append({
                                "occurrence_key": f"link:{source_hash or 'nohash'}:{page_no}:{table_index}:{source_row}",
                                "source_hash": source_hash, "source_file_name": source_file_name,
                                "source_table": table_index, "source_row": source_row,
                                "standard_name": normalize_standard_name(None, current_group),
                                "standard_name_raw": current_group, "insurer": current_insurer,
                                "product_name": row[indices["상품명"]], "rider_name": rider,
                                "amount": parse_amount(amount_text), "amount_text": amount_text,
                                "start_date": row[indices["보험시기"]].replace(".", "-"),
                                "end_date": row[indices["보험종기"]].replace(".", "-"), "source_page": page_no,
                            })
                if "상품별보험가입현황" not in compact_table:
                    continue
                header_index = next((i for i, row in enumerate(rows) if "회사 담보명" in row and "가입금액" in row), None)
                if header_index is None:
                    continue
                header = rows[header_index]
                rider_index = header.index("회사 담보명")
                standard_index = header.index("신정원 담보명") if "신정원 담보명" in header else None
                amount_index = header.index("가입금액")
                no_index = header.index("NO") if "NO" in header else None
                type_index = header.index("구분") if "구분" in header else None
                rider_numbers = [
                    number
                    for row in rows[header_index + 1:]
                    if no_index is not None and len(row) > no_index
                    for number in [_rider_number(row[no_index])]
                    if number is not None
                ]
                first_rider_no = rider_numbers[0] if rider_numbers else None
                header_state, explicit_policy = _appendix_header_policy(rows, header_index)
                if header_state == "explicit":
                    current_policy = explicit_policy
                elif header_state == "continuation" and _can_carry_appendix_identity(
                    last_appendix_page, page_no, last_appendix_rider_no, first_rider_no
                ):
                    pass
                else:
                    # Partial/new headers and unproven continuations fail closed.
                    current_policy = {}
                standard_anchor, standard_scale = _infer_standard_column_geometry(
                    page, found_table, rows, header_index + 1, standard_index
                ) if standard_index is not None else (None, 0.91)
                for row_index, row in enumerate(rows[header_index + 1:], start=header_index + 1):
                    if len(row) <= max(rider_index, amount_index) or not row[rider_index]:
                        continue
                    rider_raw = row[rider_index]
                    rider = _repair_clipped_appendix_rider(rider_raw, current_policy.get("product_name"))
                    standard_extracted_raw = row[standard_index] if standard_index is not None and len(row) > standard_index else ""
                    standard_verified, standard_verification = _verified_standard_cell_text(
                        page, found_table, row_index, standard_index, standard_anchor, standard_scale
                    ) if standard_index is not None else (None, "needs_review")
                    standard_raw = standard_verified or standard_extracted_raw
                    amount_text = row[amount_index]
                    amount = parse_amount(amount_text)
                    ledger.append({
                        "source_file_name": source_file_name, "source_hash": source_hash, "source_page": page_no,
                        "source_occurrence_key": f"ledger:{source_hash or 'nohash'}:{page_no}:{table_index}:{row_index}",
                        "insurer": current_policy.get("insurer"), "product_name": current_policy.get("product_name"),
                        "rider_no": row[no_index] if no_index is not None else None,
                        "coverage_type": row[type_index] if type_index is not None else None,
                        "rider_name": rider, "rider_name_raw": rider_raw,
                        "standard_name_extracted_raw": standard_extracted_raw,
                        "standard_name_raw": standard_raw, "standard_name": normalize_standard_name(rider, standard_raw),
                        "standard_name_verification": standard_verification,
                        "source_standard_name": None, "source_standard_names_json": "[]",
                        "source_type": "contract_ledger", "source_link_status": "unlinked",
                        "review_status": "needs_review" if standard_verification == "needs_review" else "unreviewed",
                        "amount": amount, "amount_text": amount_text, "amount_status": infer_amount_status(amount, amount_text),
                        "start_date": current_policy.get("start_date"), "end_date": current_policy.get("end_date"),
                        "confidence": 0.99, "raw_text": " | ".join(cell for cell in row if cell),
                    })
                last_appendix_page = page_no
                last_appendix_rider_no = rider_numbers[-1] if rider_numbers else None
    for link in source_links:
        amount_candidates = [row for row in ledger if row.get("amount") == link.get("amount")]
        rider_candidates = [
            row for row in amount_candidates
            if _same_source_rider(row.get("rider_name") or row.get("rider_name_raw"), link.get("rider_name"))
        ]
        period_candidates = [
            row for row in rider_candidates
            if (not link.get("start_date") or row.get("start_date") == link.get("start_date"))
            and (not link.get("end_date") or row.get("end_date") == link.get("end_date"))
        ]
        candidates = period_candidates
        identity_conflict = None
        if _known_source_identity(link.get("insurer")):
            insurer_candidates = [
                row for row in candidates if _same_source_insurer(row.get("insurer"), link.get("insurer"))
            ]
            if not insurer_candidates:
                identity_conflict = "insurer_mismatch"
            candidates = insurer_candidates
        if candidates and _known_source_identity(link.get("product_name")):
            product_candidates = [
                row for row in candidates if _same_source_rider(row.get("product_name"), link.get("product_name"))
            ]
            if not product_candidates:
                identity_conflict = "product_mismatch"
            candidates = product_candidates
        if len(candidates) > 1 and link.get("standard_name"):
            # 원본 상세표의 그룹과 원장의 검증된 신정원 분류가 정확히 같은 경우만
            # identity를 좁힌다. needs_review 원문이나 키워드 유사도는 사용하지 않는다.
            narrowed = [
                row for row in candidates
                if row.get("standard_name_verification") != "needs_review"
                and compact(row.get("standard_name_raw")) == compact(link.get("standard_name_raw"))
            ]
            candidates = narrowed or candidates
        if len(candidates) == 1:
            target = candidates[0]
            if len(compact(link.get("rider_name"))) > len(compact(target.get("rider_name"))):
                target["rider_name"] = link["rider_name"]
            names = json.loads(target.get("source_standard_names_json") or "[]")
            if link["standard_name"] not in names:
                names.append(link["standard_name"])
            target["source_standard_names_json"] = json.dumps(names, ensure_ascii=False)
            target["source_standard_name"] = names[0]
            target["source_link_status"] = "source_exact"
            resolution_status = "exact"
            resolution_reason = "identity_exact"
            linked_source_key = target.get("source_occurrence_key")
        else:
            linked_source_key = None
            resolution_status = "unresolved"
            if len(candidates) > 1:
                resolution_reason = "candidate_multiple"
            elif not amount_candidates:
                resolution_reason = "amount_mismatch"
            elif not rider_candidates:
                resolution_reason = "rider_name_mismatch"
            elif not period_candidates:
                resolution_reason = "period_mismatch"
            elif identity_conflict:
                resolution_reason = identity_conflict
            else:
                resolution_reason = "identity_mismatch"
        if len(candidates) > 1:
            for target in candidates:
                target["source_link_status"] = "needs_review"
                target["review_status"] = "needs_review"
        source_occurrences.append({
            **link,
            "source_standard_name_raw": link.get("standard_name_raw"),
            "source_standard_name": link.get("standard_name"),
            "resolution_status": resolution_status,
            "resolution_reason": resolution_reason,
            "candidate_source_keys_json": json.dumps(
                [row.get("source_occurrence_key") for row in candidates if row.get("source_occurrence_key")], ensure_ascii=False
            ),
            "linked_source_key": linked_source_key,
        })
    result = [_with_compat_fields(row) for row in ledger]
    return (result, source_occurrences) if with_occurrences else result


def parse_policy_coverages_from_pages(pages: list[tuple[int, str]], source_file_name: str | None = None, source_hash: str | None = None) -> list[dict]:
    """PDF 페이지 텍스트에서 계약별 담보 원장 후보를 추출한다. 페이지 번호 하드코딩 없음."""
    rows = _parse_appendix_pages(pages, source_file_name, source_hash)
    # 같은 담보가 요약/상세/별첨에 반복된다. 회사·상품이 정확한 별첨 원장이
    # 있으면 그것만 사용하고, 없을 때만 상세 List → 요약표 순서로 fallback한다.
    if not rows:
        rows = _parse_detail_list_pages(pages, source_file_name, source_hash)
    if not rows:
        rows = _parse_short_form_summary_pages(pages, source_file_name, source_hash)
    seen = {(r.get("source_page"), r.get("insurer"), compact(r.get("product_name")), compact(r.get("rider_name")), r.get("amount_text")) for r in rows}
    # 계약별 원장이 전혀 없는 문서만 보장별 집계표를 최종 fallback으로 사용한다.
    if not rows:
        for r in _parse_diagnosis_detail_pages(pages, source_file_name, source_hash):
            key = (r.get("source_page"), r.get("insurer"), compact(r.get("product_name")), compact(r.get("rider_name")), r.get("amount_text"))
            if key not in seen:
                rows.append(r)
                seen.add(key)
    return [_with_compat_fields(r) for r in rows]


def _match_policy_id(conn: sqlite3.Connection, customer_id: str, row: dict) -> tuple[Optional[str], str]:
    from . import repo
    candidates = repo.list_policies(conn, customer_id)
    ri, rp = compact(row.get("insurer")), compact(row.get("product_name"))
    best = None
    best_score = 0
    for p in candidates:
        score = 0
        pi, pp = compact(p.get("insurer")), compact(p.get("product_name"))
        rp_match = rp.replace("무배당", "").replace("(무)", "").replace("메리츠", "")
        pp_match = pp.replace("무배당", "").replace("(무)", "").replace("메리츠", "")
        if ri and pi and (ri in pi or pi in ri):
            score += 3
        if rp and pp and (rp in pp or pp in rp or rp_match[:12] in pp_match or pp_match[:12] in rp_match):
            score += 5
        if row.get("start_date") and row.get("start_date") == p.get("start_date"):
            score += 2
        if row.get("end_date") and row.get("end_date") == p.get("end_date"):
            score += 2
        if score > best_score:
            best, best_score = p, score
    return (best["id"], "matched") if best and best_score >= 5 else (None, "unmatched")


def _validate_policy_owner(conn: sqlite3.Connection, customer_id: str, policy_id: str) -> None:
    owned = conn.execute(
        "SELECT 1 FROM policies WHERE id = ? AND customer_id = ?", (policy_id, customer_id)
    ).fetchone()
    if owned is None:
        raise ValueError("다른 고객의 보험계약에는 담보를 연결할 수 없습니다.")


def _natural_existing(conn: sqlite3.Connection, customer_id: str, data: dict) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT * FROM policy_coverages
        WHERE customer_id = ?
          AND COALESCE(source_hash, '') = ?
          AND COALESCE(source_page, -1) = ?
          AND COALESCE(source_table, -1) = ?
          AND COALESCE(source_row, -1) = ?
          AND COALESCE(insurer, '') = ?
          AND COALESCE(product_name, '') = ?
          AND COALESCE(rider_no, '') = ?
          AND COALESCE(rider_name, '') = ?
          AND COALESCE(standard_name, '') = ?
          AND COALESCE(amount, -1) = ?
          AND COALESCE(amount_status, '') = ?
        ORDER BY created_at, id LIMIT 1
        """,
        (
            customer_id,
            str(data.get("source_hash") or ""),
            int(str(data.get("source_page"))) if data.get("source_page") is not None else -1,
            int(str(data.get("source_table"))) if data.get("source_table") is not None else -1,
            int(str(data.get("source_row"))) if data.get("source_row") is not None else -1,
            str(data.get("insurer") or ""),
            str(data.get("product_name") or ""),
            str(data.get("rider_no") or ""),
            str(data.get("rider_name") or ""),
            str(data.get("standard_name") or ""),
            -1 if data.get("amount") is None else int(data.get("amount")),
            infer_amount_status(data.get("amount"), data.get("amount_text"), data.get("amount_status")),
        ),
    ).fetchone()
    return _with_compat_fields(dict(row)) if row else None


def _insert_policy_coverage(conn: sqlite3.Connection, customer_id: str, data: dict) -> dict:
    data = {
        **data,
        "rider_name_raw": data.get("rider_name_raw") if data.get("rider_name_raw") is not None else data.get("rider_name"),
        "standard_name_raw": data.get("standard_name_raw") if data.get("standard_name_raw") is not None else data.get("standard_name"),
        "standard_name_extracted_raw": data.get("standard_name_extracted_raw") if data.get("standard_name_extracted_raw") is not None else (
            data.get("standard_name_raw") if data.get("standard_name_raw") is not None else data.get("standard_name")
        ),
        "standard_name_verification": data.get("standard_name_verification") or "raw_cell",
        "source_type": data.get("source_type") or "uploaded_pdf",
        "source_standard_names_json": data.get("source_standard_names_json") or json.dumps(
            [data["source_standard_name"]] if data.get("source_standard_name") else [], ensure_ascii=False
        ),
        "source_link_status": data.get("source_link_status") or ("source_exact" if data.get("source_standard_name") else "unlinked"),
        "review_status": data.get("review_status") or "unreviewed",
        "document_status": str(data.get("document_status") or "CONFIRMED").upper(),
        "amount_status": infer_amount_status(data.get("amount"), data.get("amount_text"), data.get("amount_status")),
    }
    if data.get("policy_id"):
        _validate_policy_owner(conn, customer_id, str(data["policy_id"]))
        data = {**data, "link_status": "provided"}
    else:
        pid, status = _match_policy_id(conn, customer_id, data)
        data = {**data, "policy_id": pid, "link_status": status}
    cid, now = _new_id(), _now()
    cols = [
        "id", "customer_id", "policy_id", "source_document_id", "source_file_name", "source_hash", "source_occurrence_key", "source_page", "source_table", "source_row",
        "insurer", "product_name", "rider_no", "coverage_type", "rider_name", "rider_name_raw",
        "standard_name", "standard_name_raw", "standard_name_extracted_raw", "standard_name_verification",
        "source_standard_name", "source_standard_names_json", "source_type", "source_link_status", "review_status",
        "amount", "amount_text", "amount_status", "rider_premium", "rider_premium_text",
        "coverage_period_text", "payment_period_text", "renewal_status", "document_status",
        "start_date", "end_date", "confidence", "raw_text", "link_status", "created_at", "updated_at",
    ]
    vals = {c: data.get(c) for c in cols}
    vals.update(id=cid, customer_id=customer_id, created_at=now, updated_at=now)
    conn.execute(
        f"INSERT INTO policy_coverages ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [vals[c] for c in cols],
    )
    return get_policy_coverage(conn, cid)


def create_policy_coverage(conn: sqlite3.Connection, customer_id: str, data: dict) -> dict:
    existing = _natural_existing(conn, customer_id, data)
    if existing:
        return existing
    created = _insert_policy_coverage(conn, customer_id, data)
    conn.commit()
    return created


def get_policy_coverage(conn: sqlite3.Connection, cid: str) -> dict:
    return _with_compat_fields(dict(conn.execute("SELECT * FROM policy_coverages WHERE id = ?", (cid,)).fetchone()))


def list_policy_coverages(conn: sqlite3.Connection, customer_id: str, policy_id: Optional[str] = None) -> list[dict]:
    if policy_id:
        rows = conn.execute(
            "SELECT * FROM policy_coverages WHERE customer_id = ? AND policy_id = ? ORDER BY source_page, rider_no, id",
            (customer_id, policy_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM policy_coverages WHERE customer_id = ? ORDER BY product_name, source_page, rider_no, id",
            (customer_id,),
        ).fetchall()
    return [_with_compat_fields(dict(r)) for r in rows]


def _decode_json_list(value) -> list:
    try:
        decoded = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def sync_source_occurrences(conn: sqlite3.Connection, customer_id: str, occurrences: list[dict]) -> dict:
    ledger_rows = conn.execute(
        "SELECT id, source_occurrence_key FROM policy_coverages WHERE customer_id=? AND source_occurrence_key IS NOT NULL",
        (customer_id,),
    ).fetchall()
    by_source_key = {str(row["source_occurrence_key"]): str(row["id"]) for row in ledger_rows}
    now = _now()
    created = updated = 0
    for occurrence in occurrences:
        occurrence_key = str(occurrence.get("occurrence_key") or "").strip()
        source_hash = str(occurrence.get("source_hash") or "").strip()
        if not occurrence_key or not source_hash:
            raise ValueError("원본 상세연결 occurrence_key와 source_hash가 필요합니다.")
        candidate_source_keys = [str(value) for value in _decode_json_list(occurrence.get("candidate_source_keys_json"))]
        candidate_ids = [by_source_key[value] for value in candidate_source_keys if value in by_source_key]
        linked_id = by_source_key.get(str(occurrence.get("linked_source_key") or ""))
        existing = conn.execute(
            "SELECT id, resolution_status FROM coverage_source_occurrences WHERE customer_id=? AND occurrence_key=?",
            (customer_id, occurrence_key),
        ).fetchone()
        values = (
            source_hash, occurrence.get("source_file_name"), int(occurrence.get("source_page") or 0),
            int(occurrence.get("source_table") or 0), int(occurrence.get("source_row") or 0),
            occurrence.get("source_standard_name_raw"), occurrence.get("source_standard_name"),
            occurrence.get("insurer"), occurrence.get("product_name"), occurrence.get("rider_name"),
            occurrence.get("amount"), occurrence.get("amount_text"), occurrence.get("start_date"), occurrence.get("end_date"),
            occurrence.get("resolution_status") or "unresolved", occurrence.get("resolution_reason") or "identity_mismatch",
            json.dumps(candidate_source_keys, ensure_ascii=False), json.dumps(candidate_ids, ensure_ascii=False), linked_id, now,
        )
        if existing:
            conn.execute(
                """UPDATE coverage_source_occurrences SET source_hash=?,source_file_name=?,source_page=?,source_table=?,source_row=?,
                   source_standard_name_raw=?,source_standard_name=?,insurer=?,product_name=?,rider_name=?,amount=?,amount_text=?,
                   start_date=?,end_date=?,
                   resolution_status=CASE WHEN resolution_status IN ('confirmed','held') THEN resolution_status ELSE ? END,
                   resolution_reason=CASE WHEN resolution_status IN ('confirmed','held') THEN resolution_reason ELSE ? END,
                   candidate_source_keys_json=?,candidate_policy_coverage_ids_json=?,
                   linked_policy_coverage_id=CASE WHEN resolution_status IN ('confirmed','held') THEN linked_policy_coverage_id ELSE ? END,
                   updated_at=? WHERE id=? AND customer_id=?""",
                (*values, existing["id"], customer_id),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO coverage_source_occurrences
                   (id,customer_id,occurrence_key,source_hash,source_file_name,source_page,source_table,source_row,
                    source_standard_name_raw,source_standard_name,insurer,product_name,rider_name,amount,amount_text,start_date,end_date,
                    resolution_status,resolution_reason,candidate_source_keys_json,candidate_policy_coverage_ids_json,
                    linked_policy_coverage_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_new_id(), customer_id, occurrence_key, *values[:-1], now, now),
            )
            created += 1
    return {"created": created, "updated": updated, "total": len(occurrences)}


def list_source_occurrences(conn: sqlite3.Connection, customer_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coverage_source_occurrences WHERE customer_id=? ORDER BY source_hash,source_page,source_table,source_row,id",
        (customer_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["candidate_source_keys"] = _decode_json_list(item.get("candidate_source_keys_json"))
        item["candidate_policy_coverage_ids"] = _decode_json_list(item.get("candidate_policy_coverage_ids_json"))
        result.append(item)
    return result


def review_source_occurrence(
    conn: sqlite3.Connection, customer_id: str, occurrence_id: str, action: str,
    policy_coverage_id: str | None = None, note: str | None = None,
) -> dict | None:
    occurrence = conn.execute(
        "SELECT * FROM coverage_source_occurrences WHERE id=? AND customer_id=?", (occurrence_id, customer_id)
    ).fetchone()
    if occurrence is None:
        return None
    if occurrence["resolution_status"] != "unresolved":
        raise ValueError("이미 검수된 원본 상세연결입니다. 최신 미해결 항목을 다시 조회하세요.")
    now = _now()
    if action == "hold":
        conn.execute(
            "UPDATE coverage_source_occurrences SET resolution_status='held',resolution_reason='user_held',linked_policy_coverage_id=NULL,review_note=?,reviewed_at=?,updated_at=? WHERE id=? AND customer_id=?",
            (note, now, now, occurrence_id, customer_id),
        )
    elif action == "link":
        if not policy_coverage_id:
            raise ValueError("연결할 계약담보를 선택해야 합니다.")
        target = conn.execute(
            "SELECT * FROM policy_coverages WHERE id=? AND customer_id=?", (policy_coverage_id, customer_id)
        ).fetchone()
        if target is None:
            raise ValueError("다른 고객이거나 존재하지 않는 계약담보입니다.")
        candidate_ids = {str(value) for value in _decode_json_list(occurrence["candidate_policy_coverage_ids_json"])}
        candidate_source_keys = {str(value) for value in _decode_json_list(occurrence["candidate_source_keys_json"])}
        if not candidate_ids:
            raise ValueError("연결 가능한 후보가 없습니다. 원본을 확인한 뒤 보류하세요.")
        if str(target["id"]) not in candidate_ids:
            raise ValueError("이 계약담보는 해당 원본 상세행의 후보가 아닙니다.")
        target_source_key = str(target["source_occurrence_key"] or "")
        if not target_source_key or target_source_key not in candidate_source_keys:
            raise ValueError("후보 원장 정보가 변경되었습니다. 다시 분석한 뒤 검수하세요.")
        if str(target["source_hash"] or "") != str(occurrence["source_hash"] or ""):
            raise ValueError("다른 원본 문서의 계약담보에는 연결할 수 없습니다.")
        names = _decode_json_list(target["source_standard_names_json"])
        source_name = occurrence["source_standard_name"]
        if source_name and source_name not in names:
            names.append(source_name)
        conn.execute(
            """UPDATE policy_coverages SET source_standard_name=?,source_standard_names_json=?,
               source_link_status='user_confirmed',review_status='reviewed',updated_at=? WHERE id=? AND customer_id=?""",
            (names[0] if names else source_name, json.dumps(names, ensure_ascii=False), now, policy_coverage_id, customer_id),
        )
        conn.execute(
            """UPDATE coverage_source_occurrences SET resolution_status='confirmed',resolution_reason='user_confirmed',
               linked_policy_coverage_id=?,review_note=?,reviewed_at=?,updated_at=? WHERE id=? AND customer_id=?""",
            (policy_coverage_id, note, now, now, occurrence_id, customer_id),
        )
    else:
        raise ValueError("action은 link 또는 hold여야 합니다.")
    return next((row for row in list_source_occurrences(conn, customer_id) if row["id"] == occurrence_id), None)


def create_many(
    conn: sqlite3.Connection, customer_id: str, items: list[dict], require_review: bool = True,
    source_occurrences: list[dict] | None = None,
) -> dict:
    if require_review is not True:
        raise ValueError("policy_coverages 저장은 검수 완료 플래그가 필요합니다.")
    if any(str(item.get("insurer") or "").strip() in {"통합보장분석", "알 수 없음"} for item in items):
        raise ValueError("표준항목 집계표는 계약별 담보 원장으로 저장할 수 없습니다.")
    # 교차 고객 policy_id를 DML 전에 전부 검증해 mixed bulk의 부분 저장을 막는다.
    for item in items:
        if item.get("policy_id"):
            _validate_policy_owner(conn, customer_id, str(item["policy_id"]))
    created: list[dict] = []
    skipped: list[dict] = []
    try:
        for item in items:
            existing = _natural_existing(conn, customer_id, item)
            if existing:
                skipped.append(existing)
                continue
            created.append(_insert_policy_coverage(conn, customer_id, item))
        occurrence_result = sync_source_occurrences(conn, customer_id, source_occurrences or [])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "created": len(created),
        "skipped_existing": len(skipped),
        "policy_link_failed": sum(1 for r in created if not r.get("policy_id")),
        "items": created,
        "existing_items": skipped,
        "source_occurrences": occurrence_result,
    }


def attach_coverages_to_policies(conn: sqlite3.Connection, customer: dict) -> dict:
    coverages = list_policy_coverages(conn, customer["id"])
    by_policy: dict[str, list[dict]] = {}
    for c in coverages:
        if c.get("policy_id"):
            by_policy.setdefault(c["policy_id"], []).append(c)
    for p in customer.get("policies") or []:
        p["coverages"] = by_policy.get(p["id"], [])
        p["coverage_count"] = len(p["coverages"])
    customer["policy_coverages"] = coverages
    return customer
