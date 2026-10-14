"""
M보장분석 parser
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import pdfplumber

try:  # 기존 표준담보 정규화 로직 재사용
    from .policy_coverages import normalize_standard_name
except Exception:  # pragma: no cover
    def normalize_standard_name(company_name: str | None, standard_name: str | None = None) -> str:
        return (standard_name or company_name or "기타").strip() or "기타"

try:
    from .coverage_mapping import STANDARD_COVERAGE_CATEGORIES, normalize_coverage_name
except Exception:  # pragma: no cover
    from coverage_mapping import STANDARD_COVERAGE_CATEGORIES, normalize_coverage_name


@dataclass
class ParsedCoverage:
    """파싱된 담보 정보"""

    insurer: str
    product_name: str
    coverage_name: str
    coverage_period: Optional[str]
    payment_period: Optional[str]
    amount: int
    amount_text: str
    source_page: int
    source_type: str  # "detail_list", "product_detail", "category_detail"
    confidence: float
    category: str = "기타"


def parse_coverage_analysis(pdf_path: str, candidate_pages: dict) -> List[ParsedCoverage]:
    """
    M보장분석 파싱

    보장별상세표(합산 금액)를 우선 사용. 50개 담보 정확도 최우선.
    """
    coverages: List[ParsedCoverage] = []

    # 보장별상세표를 최우선으로 사용 (합산 금액)
    category_detail_pages = candidate_pages.get("category_detail_pages", [])
    if category_detail_pages:
        coverages.extend(_parse_category_detail_pages(pdf_path, category_detail_pages))

    result = _deduplicate_coverages(coverages)
    for cov in result:
        cov.coverage_name = normalize_coverage_name(cov.coverage_name)
        cov.category = _categorize_coverage(cov.coverage_name)
    return result


def _categorize_coverage(coverage_name: str) -> str:
    """
    담보명 → 카테고리 분류

    Returns:
        "사망", "암", "뇌혈관", "심장", "수술비", "입원비", "운전자", "기타"
    """
    normalized = normalize_coverage_name(coverage_name)

    for category, coverage_list in STANDARD_COVERAGE_CATEGORIES.items():
        if normalized in coverage_list:
            return category

    if "사망" in coverage_name:
        return "사망"
    elif "암" in coverage_name or "항암" in coverage_name:
        return "암"
    elif "뇌" in coverage_name:
        return "뇌혈관"
    elif "심장" in coverage_name or "심근경색" in coverage_name:
        return "심장"
    elif "수술" in coverage_name:
        return "수술비"
    elif "입원" in coverage_name:
        return "입원비"
    elif "운전" in coverage_name or "교통" in coverage_name:
        return "운전자"
    elif "골절" in coverage_name or "화상" in coverage_name:
        return "기타"

    return "기타"


def _parse_detail_list_pages(pdf_path: str, pages: List[int]) -> List[ParsedCoverage]:
    """가입담보상세List 페이지 파싱"""
    coverages: List[ParsedCoverage] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in pages:
            if page_num < 1 or page_num > len(pdf.pages):
                continue

            page = pdf.pages[page_num - 1]
            text = page.extract_text() or ""
            # 후보 탐지에서 별첨 상품별 보험가입현황의 NO 헤더도 잡히므로 제외한다.
            if "가입담보 상세 List" not in text and "가입담보상세List" not in text:
                continue

            for table in page.extract_tables() or []:
                header_row = _find_header_row(table, ["회사명", "상품명", "담보명", "가입금액"])
                if header_row is None:
                    continue

                header = table[header_row]
                insurer_col = _find_col_index(header, ["보험사", "회사명", "회사"])
                product_col = _find_col_index(header, ["상품명", "보험상품명"])
                coverage_col = _find_col_index(header, ["담보명", "보장명"])
                amount_col = _find_col_index(header, ["가입금액", "보험가입금액"])
                start_col = _find_col_index(header, ["보험시기", "보험기간시작"])
                end_col = _find_col_index(header, ["보험종기", "보험기간끝"])

                current_insurer = ""
                current_product = ""

                for row in table[header_row + 1:]:
                    if not row or all(not cell for cell in row):
                        continue

                    # 보험사/상품명은 첫 행에만 있고 이후 빈 칸으로 이어진다.
                    insurer = _get_cell(row, insurer_col)
                    if insurer:
                        current_insurer = insurer

                    product = _get_cell(row, product_col)
                    if product:
                        current_product = product

                    coverage_name = _get_cell(row, coverage_col)
                    if not coverage_name or len(coverage_name) < 2:
                        continue

                    amount_text = _get_cell(row, amount_col)
                    if not amount_text:
                        continue
                    amount = _parse_amount(amount_text)

                    if not current_insurer or not current_product:
                        continue

                    period = _join_period(_get_cell(row, start_col), _get_cell(row, end_col))
                    coverages.append(ParsedCoverage(
                        insurer=current_insurer,
                        product_name=current_product,
                        coverage_name=coverage_name,
                        coverage_period=period,
                        payment_period=None,
                        amount=amount,
                        amount_text=amount_text,
                        source_page=page_num,
                        source_type="detail_list",
                        confidence=0.95,
                    ))

    return coverages


def _parse_product_detail_pages(pdf_path: str, pages: List[int]) -> List[ParsedCoverage]:
    """상품별가입담보상세 페이지 파싱(현재 M보장분석 fixture에는 별도 후보 없음)."""
    return []


def _parse_category_detail_pages(pdf_path: str, pages: List[int]) -> List[ParsedCoverage]:
    """보장별상세표 페이지 파싱"""
    coverages: List[ParsedCoverage] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num in pages:
            if page_num < 1 or page_num > len(pdf.pages):
                continue
            page = pdf.pages[page_num - 1]
            text = page.extract_text() or ""
            if "보장별 상세" not in text and "보장별상세표" not in text:
                continue

            for table in page.extract_tables() or []:
                header_row = _find_header_row(table, ["보장상세", "권장금액", "가입금액", "과부족"])
                if header_row is None:
                    continue
                header = table[header_row]
                group_col = _find_col_index(header, ["보장군"])
                coverage_col = _find_col_index(header, ["보장상세", "담보명", "보장명"])
                amount_col = _find_col_index(header, ["가입금액"])
                status_col = _find_col_index(header, ["과부족"])

                current_group = ""
                for row in table[header_row + 1:]:
                    if not row or all(not cell for cell in row):
                        continue
                    group = _get_cell(row, group_col)
                    if group:
                        current_group = group
                    coverage_name = _get_cell(row, coverage_col)
                    amount_text = _get_cell(row, amount_col)
                    status = _get_cell(row, status_col)
                    if not coverage_name or status not in {"충분", "부족", "미가입", "확인필요"}:
                        continue
                    coverages.append(ParsedCoverage(
                        insurer="통합보장분석",
                        product_name="M·보장분석",
                        coverage_name=coverage_name,
                        coverage_period=current_group or None,
                        payment_period=None,
                        amount=_parse_amount(amount_text),
                        amount_text=amount_text,
                        source_page=page_num,
                        source_type="category_detail",
                        confidence=0.9,
                    ))

    return coverages


def _deduplicate_coverages(coverages: List[ParsedCoverage]) -> List[ParsedCoverage]:
    """중복 제거"""
    seen = set()
    result: List[ParsedCoverage] = []

    for cov in coverages:
        # 보험사 + 상품명 + 담보명 + 금액 조합으로 중복 체크
        key = (
            _compact(cov.insurer),
            _compact(cov.product_name),
            _compact(cov.coverage_name),
            _compact(cov.amount_text),
        )
        if key not in seen:
            seen.add(key)
            result.append(cov)

    return result


def _find_header_row(table: list, keywords: list) -> Optional[int]:
    """헤더 행 찾기"""
    for i, row in enumerate(table):
        if not row:
            continue
        text = " ".join(str(cell or "") for cell in row)
        if sum(1 for kw in keywords if kw in text) >= min(2, len(keywords)):
            return i
    return None


def _find_col_index(header: list, keywords: list) -> Optional[int]:
    """컬럼 인덱스 찾기"""
    for i, cell in enumerate(header):
        if cell and any(kw in str(cell) for kw in keywords):
            return i
    return None


def _get_cell(row: list, col_index: Optional[int]) -> str:
    """셀 값 가져오기"""
    if col_index is None or col_index >= len(row):
        return ""
    cell = row[col_index]
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _parse_amount(text: str) -> int:
    """금액 파싱"""
    if not text:
        return 0

    text = str(text).replace(",", "").replace("원", "").strip()

    amount = 0
    eok_match = re.search(r"(\d+(?:\.\d+)?)\s*억", text)
    man_match = re.search(r"(\d+(?:\.\d+)?)\s*만", text)
    cheon_match = re.search(r"(\d+(?:\.\d+)?)\s*천", text)
    baek_match = re.search(r"(\d+(?:\.\d+)?)\s*백", text)
    sip_match = re.search(r"(\d+(?:\.\d+)?)\s*십", text)

    if eok_match:
        amount += int(float(eok_match.group(1)) * 100_000_000)
    if man_match:
        amount += int(float(man_match.group(1)) * 10_000)
    elif cheon_match:
        amount += int(float(cheon_match.group(1)) * 10_000_000)
    elif baek_match:
        amount += int(float(baek_match.group(1)) * 1_000_000)
    elif sip_match:
        amount += int(float(sip_match.group(1)) * 100_000)

    if amount > 0:
        return amount

    num_match = re.search(r"\d+(?:\.\d+)?", text)
    if num_match:
        return int(float(num_match.group(0)))

    return 0


def _join_period(start: str, end: str) -> Optional[str]:
    if start and end:
        return f"{start}~{end}"
    return start or end or None


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _standard_key(name: str) -> str:
    return _compact(normalize_standard_name(name, name))
