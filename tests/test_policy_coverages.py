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
    assert all(r["product_name"].startswith("무배당 삼성화재 건강보험 New내돈내삼") for r in rows)


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
