import sqlite3

from database import policy_coverages
from database.db import connect, init_schema
from database import repo


def test_policy_coverages_appendix_parser_normalizes_required_rows():
    page = """
별첨 상품별 보험가입현황 27
삼성화재
무배당 삼성화재 건강보험 New내돈내삼1640(2412.2)(납입면제,해약환급금 미지급형)
계약자/피보험자 나*원 납입주기/납입기간/만기 매월납/56년/90세 만기
보험기간 2025.02.14~2081.02.14 월 보험료 92,734원
가입담보명 및 가입금액 (단위 : 원)
NO 구분 회사 담보명 신정원 담보명 가입금액
4 정액 [건강]뇌혈관질환 진단비 뇌혈관질환진단 3,000만
5 정액 [건강]허혈성심장질환 진단비 허혈성심장질환진단 3,000만
10 정액 [건강]질병 1~5종 수술비(5종) 질병종수술 1,000만
"""
    rows = policy_coverages.parse_policy_coverages_from_pages([(28, page)], "sample.pdf", "hash")
    names = {(r["rider_name"], r["standard_name"], r["amount_text"], r["source_page"]) for r in rows}
    assert ("[건강]뇌혈관질환 진단비", "뇌혈관 진단비", "3,000만", 28) in names
    assert ("[건강]허혈성심장질환 진단비", "허혈성심장질환 진단비", "3,000만", 28) in names
    assert ("[건강]질병 1~5종 수술비(5종)", "질병종수술", "1,000만", 28) in names
    assert all(r["coverage_name"] == r["rider_name"] for r in rows)
    assert all(r["current_amount"] == r["amount_text"] for r in rows)
    assert all(r["product_name"].startswith("무배당 삼성화재 건강보험 New내돈내삼") for r in rows)


def test_policy_coverages_diagnosis_detail_parser_handles_pdf_oxide_layout():
    page = """
04
진단상세보장별 상세
(단위: 원)
보장별 상세 내역
보장군 보장상세 권장금액 가입금액 부족금액 과부족
질병사망 1억 3억9,000만 -2억9,000만 충분
상해사망 2억 5억9,000만 -3억9,000만 충분
질병 3%이상후유장해 5,000만 3,000만 2,000만 부족
일반암진단비 1억5,000만 1억6,000만 -1,000만 충분
교통사고처리 지원금
운전자
"""
    rows = policy_coverages.parse_policy_coverages_from_pages([(5, page)], "oxide.pdf", "hash")
    assert len(rows) == 4
    by_name = {r["rider_name"]: r for r in rows}
    assert by_name["일반암진단비"]["amount"] == 160_000_000
    assert by_name["질병 3%이상후유장해"]["amount_text"] == "3,000만"
    assert all(r["product_name"] == "보장분석 상세" for r in rows)


def test_policy_coverages_short_form_summary_parser_creates_37_rows():
    page2 = """
용귀례 님의 전체 보장현황 2026-09-23 15:37:50
(70세 ,여자)
※ 기준담보/권장금액 : 기본형(37개)/개인설정
상해사망 1억 1,000만 - 1억 1,000만 -
질병사망 - - - - -
상해80%미만후유장해 1억 1,000만 - 1억 1,000만 -
질병80%미만후유장해 - - - - -
장기요양간병비 - - - - -
경증치매진단 200만 - 200만 - -
간병인/간호간병상해일당 - - - - -
간병인/간호간병질병일당 - - - - -
일반암 3,000만 - - 3,000만 -
유사암 600만 - - 600만 -
고액암 - - - - -
고액(표적)항암치료비 - - - - -
뇌혈관질환 - - - - -
뇌졸중 - - - - -
뇌출혈 1,000만 - - 1,000만 -
허혈성심장질환 - - - - -
급성심근경색증 1,000만 - - 1,000만 -
"""
    page3 = """
용귀례 님의 전체 보장현황 2026-09-23 15:37:50
상해입원의료비 - - - - -
상해통원의료비 - - - - -
질병입원의료비 - - - - -
질병통원의료비 - - - - -
3대비급여실손 - - - - -
상해수술비 100만 - - 100만 -
질병수술비 50만 - - 50만 -
암수술비 - - - - -
뇌혈관질환수술비 - - - - -
허혈성심장질환수술비 - - - - -
상해입원일당 3만 - 3만 - -
질병입원일당 6만 - 3만 3만 -
벌금(대인/스쿨존/대물) - - - - -
교통사고처리지원금 - - - - -
변호사선임비용 - - - - -
자동차사고부상 - - - - -
골절진단비 580만 - 550만 30만 -
보철치료비 - - - - -
가족/일상/자녀배상 - - - - -
화재벌금 - - - - -
"""
    rows = policy_coverages.parse_policy_coverages_from_pages([(2, page2), (3, page3)], "short.pdf", "hash")
    assert len(rows) == 37
    by_name = {r["rider_name"]: r for r in rows}
    assert by_name["일반암"]["standard_name"] == "일반암 진단비"
    assert by_name["일반암"]["amount"] == 30_000_000
    assert by_name["질병사망"]["amount"] == 0
    assert by_name["질병입원일당"]["standard_name"] == "질병일당"
    assert all(r["insurer"] == "알 수 없음" and r["product_name"] == "보장분석 요약" for r in rows)


def test_policy_coverages_bulk_requires_review_and_links_policy(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "나상원"})
    policy = repo.create_policy(conn, customer["id"], {
        "insurer": "삼성화재",
        "product_name": "무배당 삼성화재 건강보험 New내돈내삼1640",
        "premium": 92734,
        "start_date": "2025-02-14",
        "end_date": "2081-02-14",
    })
    item = {
        "insurer": "삼성화재",
        "product_name": "무배당 삼성화재 건강보험 New내돈내삼1640(2412.2)",
        "rider_name": "[건강]질병 1~5종 수술비(5종)",
        "standard_name": "질병종수술",
        "amount_text": "1,000만",
        "amount": 10_000_000,
        "start_date": "2025-02-14",
        "end_date": "2081-02-14",
    }
    try:
        policy_coverages.create_many(conn, customer["id"], [item], require_review=False)
        assert False, "review flag must be required"
    except ValueError:
        pass
    result = policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    assert result["created"] == 1
    assert result["policy_link_failed"] == 0
    saved = result["items"][0]
    assert saved["policy_id"] == policy["id"]
