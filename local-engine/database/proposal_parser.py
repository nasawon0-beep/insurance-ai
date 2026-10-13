"""
가입제안서 parser
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import pdfplumber


@dataclass
class ProposalRider:
    """가입제안서 특약 정보"""
    rider_type: str  # "기본계약" or "특약"
    rider_name: str
    coverage_period: Optional[str]
    payment_period: Optional[str]
    coverage_amount: int
    coverage_amount_text: str
    premium: Optional[int]
    premium_text: Optional[str]


@dataclass
class ProposalInfo:
    """가입제안서 기본 정보"""
    insurer: str
    product_name: str
    plan_name: Optional[str]
    contract_period: Optional[str]
    payment_method: Optional[str]
    first_premium: Optional[int]
    first_premium_text: Optional[str]
    riders: List[ProposalRider]


def parse_proposal(pdf_path: str, candidate_pages: dict) -> ProposalInfo:
    """
    가입제안서 파싱

    Args:
        pdf_path: PDF 파일 경로
        candidate_pages: detect_candidate_pages() 결과

    Returns:
        ProposalInfo
    """
    proposal_pages = candidate_pages.get("proposal_table_pages", [])
    if not proposal_pages:
        proposal_pages = _find_proposal_pages(pdf_path)

    riders: List[ProposalRider] = []
    insurer = ""
    product_name = ""
    plan_name = None
    contract_period = None
    payment_method = None
    first_premium = None
    first_premium_text = None

    with pdfplumber.open(pdf_path) as pdf:
        if pdf.pages:
            first_page_text = pdf.pages[0].extract_text() or ""
            insurer = _extract_insurer(first_page_text)
            product_name = _extract_product_name(first_page_text)
            plan_name = _extract_plan_name(first_page_text)
            first_premium, first_premium_text = _extract_first_premium(pdf.pages[0])

        for page_num in proposal_pages:
            if page_num <= len(pdf.pages):
                page = pdf.pages[page_num - 1]
                riders.extend(_extract_riders_from_page(page))
                page_contract_period, page_payment_method = _extract_contract_terms(page)
                contract_period = contract_period or page_contract_period
                payment_method = payment_method or page_payment_method
                if not first_premium:
                    first_premium, first_premium_text = _extract_first_premium(page)

        # 가입사항 요약표가 짧게 나온 암보험 제안서는 다음 보장내역 표의 세부 담보를
        # 최소 검수 가능한 수준(8~10개)까지만 보강한다. 제안서는 보유담보 원장 저장용이
        # 아니므로 상세 보장내역 전체를 중복 적재하지 않는다.
        if len(riders) < 8:
            seen = {_rider_key(r) for r in riders}
            for page in pdf.pages:
                for rider in _extract_benefit_riders_from_page(page):
                    key = _rider_key(rider)
                    if key in seen:
                        continue
                    riders.append(rider)
                    seen.add(key)
                    if len(riders) >= 10:
                        break
                if len(riders) >= 10:
                    break

    return ProposalInfo(
        insurer=insurer,
        product_name=product_name,
        plan_name=plan_name,
        contract_period=contract_period,
        payment_method=payment_method,
        first_premium=first_premium,
        first_premium_text=first_premium_text,
        riders=_dedupe_riders(riders),
    )


def _find_proposal_pages(pdf_path: str) -> List[int]:
    """가입사항 페이지 찾기"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if "가입사항" in text and ("특약명" in text or "담보명" in text):
                pages.append(i + 1)
    return pages


def _extract_insurer(text: str) -> str:
    """보험사명 추출"""
    insurers = [
        "삼성화재", "현대해상", "DB손해보험", "DB손보", "KB손해보험", "KB손보",
        "메리츠화재", "메리츠", "한화손해보험", "한화손보", "흥국화재", "MG손해보험",
        "삼성생명", "한화생명", "교보생명", "신한라이프", "AIA생명", "푸르덴셜생명",
    ]
    for ins in insurers:
        if ins in text:
            return "메리츠화재" if ins == "메리츠" else ins
    # 실제 굿리치 제안서 표지에는 보험사명이 빠지고 상품 브랜드만 있는 경우가 있다.
    if "더핏" in text:
        return "메리츠화재"
    return ""


def _extract_product_name(text: str) -> str:
    """상품명 추출"""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for i, line in enumerate(lines):
        if "상품명" in line or "보험상품명" in line:
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    for line in lines:
        if "보험" in line and ("무)" in line or "무배당" in line or "(무)" in line):
            return line.strip()
    return ""


def _extract_plan_name(text: str) -> Optional[str]:
    """플랜명 추출"""
    lines = text.split("\n")
    for line in lines:
        if "플랜" in line:
            return line.strip()
    return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def _extract_riders_from_page(page) -> List[ProposalRider]:
    """페이지에서 특약 추출 (가입사항 테이블 기반)"""
    riders: List[ProposalRider] = []
    tables = page.extract_tables()
    if not tables:
        return riders

    for table in tables:
        header_row = None
        for i, row in enumerate(table):
            if row and any(cell and ("특약명" in str(cell) or "담보명" in str(cell)) for cell in row):
                if any(cell and "가입금액" in str(cell) for cell in row):
                    header_row = i
                    break
        if header_row is None:
            continue

        header = table[header_row]
        type_col = _find_col_index(header, ["구분"])
        rider_name_col = _find_col_index(header, ["특약명", "담보명", "보장명"])
        coverage_amount_col = _find_col_index(header, ["가입금액", "보험가입금액", "보장금액"])
        premium_col = _find_col_index(header, ["보험료", "월보험료"])
        period_col = _find_col_index(header, ["보험기간", "보장기간"])
        payment_col = _find_col_index(header, ["납입기간"])

        current_type = "특약"
        for row in table[header_row + 1:]:
            if not row or all(not cell for cell in row):
                continue
            row_text = " ".join(_clean_text(str(c)) for c in row if c)
            if "납입보험료" in row_text or "보험료소계" in row_text:
                continue

            raw_type = _get_cell(row, type_col)
            if raw_type:
                current_type = "기본계약" if "기본" in raw_type else "특약"
            rider_name = _clean_text(_get_cell(row, rider_name_col))
            if not rider_name or len(rider_name) < 2:
                continue

            amount_text = _clean_text(_get_cell(row, coverage_amount_col))
            amount = _parse_amount(amount_text)
            if amount <= 0:
                continue

            premium_text = _clean_text(_get_cell(row, premium_col))
            premium = _parse_amount(premium_text) if premium_text else None
            coverage_period = _clean_text(_get_cell(row, period_col)) or None
            payment_period = _clean_text(_get_cell(row, payment_col)) or None

            riders.append(ProposalRider(
                rider_type=current_type,
                rider_name=rider_name,
                coverage_period=coverage_period,
                payment_period=payment_period,
                coverage_amount=amount,
                coverage_amount_text=amount_text or "",
                premium=premium,
                premium_text=premium_text or None,
            ))
    return riders


def _extract_benefit_riders_from_page(page) -> List[ProposalRider]:
    """보장내역 표에서 세부 담보를 보강 추출한다."""
    riders: List[ProposalRider] = []
    for table in page.extract_tables() or []:
        header_row = None
        for i, row in enumerate(table):
            if row and any(cell and "특약명" in str(cell) for cell in row) and any(cell and "지급금액" in str(cell) for cell in row):
                header_row = i
                break
        if header_row is None:
            continue
        header = table[header_row]
        type_col = _find_col_index(header, ["구분"])
        rider_name_col = _find_col_index(header, ["특약명"])
        premium_col = _find_col_index(header, ["보험료"])
        period_col = _find_col_index(header, ["보험기간"])
        benefit_name_col = _find_col_index(header, ["담보명"])
        benefit_amount_col = _find_col_index(header, ["지급금액"])
        current_type = "특약"
        current_name = ""
        for row in table[header_row + 1:]:
            if not row or all(not cell for cell in row):
                continue
            raw_type = _get_cell(row, type_col)
            if raw_type:
                current_type = "기본계약" if "기본" in raw_type else "특약"
            raw_name = _clean_text(_get_cell(row, rider_name_col))
            if raw_name:
                current_name = raw_name
                # 가입사항 표에 이미 잡힌 특약 대표 행은 중복 보강하지 않고,
                # 같은 특약 아래 담보명만 추가로 있는 행(raw_name 없음)만 사용한다.
                continue
            benefit_name = _clean_text(_get_cell(row, benefit_name_col))
            name = benefit_name or current_name
            amount_text = _clean_text(_get_cell(row, benefit_amount_col))
            amount = _parse_amount(amount_text)
            if not name or len(name) < 2 or amount <= 0:
                continue
            premium_text = _clean_text(_get_cell(row, premium_col))
            riders.append(ProposalRider(
                rider_type=current_type,
                rider_name=name,
                coverage_period=_clean_text(_get_cell(row, period_col)) or None,
                payment_period=None,
                coverage_amount=amount,
                coverage_amount_text=amount_text,
                premium=_parse_amount(premium_text) if premium_text else None,
                premium_text=premium_text or None,
            ))
    return riders


def _find_col_index(header: list, keywords: list) -> Optional[int]:
    """헤더에서 컬럼 인덱스 찾기"""
    for i, cell in enumerate(header):
        if cell and any(kw in str(cell) for kw in keywords):
            return i
    return None


def _get_cell(row: list, col_index: Optional[int]) -> str:
    """테이블 셀 값 가져오기"""
    if col_index is None or col_index >= len(row):
        return ""
    cell = row[col_index]
    return str(cell).strip() if cell else ""


def _parse_amount(text: str) -> int:
    """금액 텍스트 파싱"""
    if not text:
        return 0
    text = text.replace(",", "").replace("\n", " ").strip()
    amount = 0
    eok_match = re.search(r"(\d+(?:\.\d+)?)\s*억", text)
    man_match = re.search(r"(\d+(?:\.\d+)?)\s*만", text)
    if eok_match:
        amount += int(float(eok_match.group(1)) * 100000000)
    if man_match:
        amount += int(float(man_match.group(1)) * 10000)
    if amount > 0:
        return amount
    won_matches = re.findall(r"(\d+)\s*원", text)
    if won_matches:
        return int(won_matches[-1])
    nums = re.findall(r"\d+", text)
    if nums:
        return int(nums[-1])
    return 0


def _extract_first_premium(page) -> tuple[Optional[int], Optional[str]]:
    """1회보험료 추출"""
    text = page.extract_text() or ""
    lines = text.split("\n")
    for line in lines:
        if "1회보험료" in line or "총 보험료" in line or "납입보험료" in line:
            amount = _parse_amount(line)
            if amount > 0:
                return amount, line.strip()
    return None, None


def _extract_contract_terms(page) -> tuple[Optional[str], Optional[str]]:
    text = page.extract_text() or ""
    contract_period = None
    payment_method = None
    m = re.search(r"계약기간\s*([^\n]+?까지[^\n]*)", text)
    if m:
        contract_period = _clean_text(m.group(1))
    m = re.search(r"납입방법\s*([가-힣A-Za-z0-9]+)", text)
    if m:
        payment_method = m.group(1)
    return contract_period, payment_method


def _rider_key(rider: ProposalRider) -> tuple[str, int]:
    name = re.sub(r"\s+", "", rider.rider_name)
    # 1~5종 수술처럼 같은 특약을 종별로 반복한 행은 제안서 검수에서는 하나로 묶는다.
    grouped = False
    if re.search(r"상해1~5종수술보장\(수술당1회한\)", name):
        name = "상해1~5종수술보장"
        grouped = True
    name = re.sub(r"_?[1-5]종수술$", "종수술", name)
    return (name, 0 if grouped else rider.coverage_amount)


def _dedupe_riders(riders: List[ProposalRider]) -> List[ProposalRider]:
    out: List[ProposalRider] = []
    seen: set[tuple[str, int]] = set()
    for rider in riders:
        key = _rider_key(rider)
        if key in seen:
            continue
        seen.add(key)
        out.append(rider)
    return out
