"""계약별 담보 원장 저장/조회 및 PDF 추출 보조.

보장분석 탭(consultations.coverage_json)과 분리된, 고객 상세 > 가입목록용
계약별 담보 원장(policy_coverages) 전용 모듈이다.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


_AMOUNT_RE = re.compile(r"(?P<amount>\d[\d,]*(?:\.\d+)?\s*(?:억|천|백|십)?\s*만?|\d[\d,]*\s*원)(?:\s|$)")
_DATE_RE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}")
_ROW_RE = re.compile(
    r"^(?P<no>\d{1,3})\s+(?P<ctype>실손|정액|기타)\s+"
    r"(?P<body>.+?)\s+(?P<amount>\d[\d,]*(?:\.\d+)?\s*(?:억|천|백|십)?\s*만|\d[\d,]*\s*원|\d[\d,]*)$"
)
_DETAIL_ROW_RE = re.compile(
    r"^(?:(?P<std>[가-힣A-Za-z0-9%·/()\-+]+)\s+)?"
    r"(?P<insurer>삼성화재|메리츠화재|메리츠|한화생명|한화손보|현대해상|KB손보|DB손보|ABL생명|흥국화재|하나손보|MG손보|라이나생명|신한라이프생명|흥국생명)\s+"
    r"(?P<rest>.+?)\s+(?P<amount>\d[\d,]*(?:\.\d+)?\s*(?:억|천|백|십)?\s*만|\d[\d,]*\s*원|\d[\d,]*)\s+"
    r"(?P<start>\d{4}[-.]\d{2}[-.]\d{2})\s+(?P<end>\d{4}[-.]\d{2}[-.]\d{2})$"
)

_STD_ALIASES: list[tuple[str, str]] = [
    ("질병1~5종수술비", "질병종수술"), ("질병1-5종수술비", "질병종수술"), ("질병N종수술비", "질병종수술"), ("질병종수술", "질병종수술"),
    ("상해1~5종수술비", "상해종수술"), ("상해1-5종수술비", "상해종수술"), ("상해N종수술비", "상해종수술"), ("상해종수술", "상해종수술"),
    ("뇌혈관질환진단비", "뇌혈관 진단비"), ("뇌혈관질환진단", "뇌혈관 진단비"), ("뇌혈관진단비", "뇌혈관 진단비"),
    ("허혈성심장질환진단비", "허혈성심장질환 진단비"), ("허혈성심장질환진단", "허혈성심장질환 진단비"), ("허혈심장질환진단", "허혈성심장질환 진단비"),
    ("유사암진단비", "유사암 진단비"), ("유사암진단", "유사암 진단비"), ("기타피부암", "유사암 진단비"), ("갑상선암", "유사암 진단비"), ("제자리암", "유사암 진단비"), ("경계성종양", "유사암 진단비"),
    ("통합암진단비(유사암제외)", "통합암 진단비"), ("통합암진단비", "통합암 진단비"), ("통합암", "통합암 진단비"),
    ("전이암진단비", "특정암 진단비"), ("림프절전이암진단비", "특정암 진단비"), ("특정전이암진단비", "특정암 진단비"), ("10대특정암", "특정암 진단비"), ("15대특정암", "특정암 진단비"), ("4대고액암", "특정암 진단비"), ("특정암진단", "특정암 진단비"),
]


def compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def normalize_standard_name(company_name: str | None, standard_name: str | None = None) -> str:
    hay = compact((company_name or "") + " " + (standard_name or ""))
    for needle, canonical in _STD_ALIASES:
        if compact(needle) in hay:
            return canonical
    return (standard_name or "기타").strip() or "기타"


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
    if m and "천" not in s and "백" not in s and "십" not in s:
        total += int(float(m.group(1)) * 10_000)
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
    for i, line in enumerate(lines[:8]):
        if i == 1 and line.strip() not in {"가입담보명 및 가입금액 (단위 : 원)", "NO 구분 회사 담보명 신정원 담보명 가입금액"}:
            insurer = line.strip()
        if i == 2 and "NO 구분" not in line:
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
        if "별첨 상품별 보험가입현황" in text or "상품별 가입담보상세" in text:
            pol = _policy_from_appendix_page(lines)
            if pol.get("insurer") or pol.get("product_name"):
                current_policy = pol
        if "가입담보명 및 가입금액" not in text and "상품별 가입담보상세" not in text:
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
                "coverage_type": m.group("ctype"),
                "rider_name": company,
                "standard_name": normalize_standard_name(company, std),
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
                "standard_name": normalize_standard_name(rider, std),
                "amount": parse_amount(m.group("amount")),
                "amount_text": m.group("amount"),
                "start_date": m.group("start").replace(".", "-"),
                "end_date": m.group("end").replace(".", "-"),
                "confidence": 0.82,
                "raw_text": line,
            })
    return rows


def parse_policy_coverages_from_pages(pages: list[tuple[int, str]], source_file_name: str | None = None, source_hash: str | None = None) -> list[dict]:
    """PDF 페이지 텍스트에서 계약별 담보 원장 후보를 추출한다. 페이지 번호 하드코딩 없음."""
    rows = _parse_appendix_pages(pages, source_file_name, source_hash)
    seen = {(r.get("source_page"), r.get("insurer"), compact(r.get("product_name")), compact(r.get("rider_name")), r.get("amount_text")) for r in rows}
    for r in _parse_detail_list_pages(pages, source_file_name, source_hash):
        key = (r.get("source_page"), r.get("insurer"), compact(r.get("product_name")), compact(r.get("rider_name")), r.get("amount_text"))
        if key not in seen:
            rows.append(r)
            seen.add(key)
    return rows


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


def create_policy_coverage(conn: sqlite3.Connection, customer_id: str, data: dict) -> dict:
    if not data.get("policy_id"):
        pid, status = _match_policy_id(conn, customer_id, data)
        data = {**data, "policy_id": pid, "link_status": status}
    else:
        data = {**data, "link_status": "provided"}
    cid, now = _new_id(), _now()
    cols = [
        "id", "customer_id", "policy_id", "source_document_id", "source_file_name", "source_hash", "source_page",
        "insurer", "product_name", "rider_no", "coverage_type", "rider_name", "standard_name", "amount", "amount_text",
        "start_date", "end_date", "confidence", "raw_text", "link_status", "created_at", "updated_at",
    ]
    vals = {c: data.get(c) for c in cols}
    vals.update(id=cid, customer_id=customer_id, created_at=now, updated_at=now)
    conn.execute(
        f"INSERT INTO policy_coverages ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [vals[c] for c in cols],
    )
    conn.commit()
    return get_policy_coverage(conn, cid)


def get_policy_coverage(conn: sqlite3.Connection, cid: str) -> dict:
    return dict(conn.execute("SELECT * FROM policy_coverages WHERE id = ?", (cid,)).fetchone())


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
    return [dict(r) for r in rows]


def create_many(conn: sqlite3.Connection, customer_id: str, items: list[dict], require_review: bool = True) -> dict:
    if require_review is not True:
        raise ValueError("policy_coverages 저장은 검수 완료 플래그가 필요합니다.")
    created = [create_policy_coverage(conn, customer_id, item) for item in items]
    return {
        "created": len(created),
        "policy_link_failed": sum(1 for r in created if not r.get("policy_id")),
        "items": created,
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
