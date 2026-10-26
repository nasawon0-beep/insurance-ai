import json
import sqlite3

import pytest

from database import coverage, policy_coverages
from database.db import connect, init_schema
from database import repo


def test_standard_mapping_keeps_distinct_surgery_and_brain_diagnoses():
    assert policy_coverages.normalize_standard_name("질병수술비") == "질병수술비"
    assert policy_coverages.normalize_standard_name("질병종수술") == "질병종수술"
    assert policy_coverages.normalize_standard_name("상해수술비") == "상해수술비"
    assert policy_coverages.normalize_standard_name("상해종수술") == "상해종수술"
    assert policy_coverages.normalize_standard_name("뇌졸중진단비") == "뇌졸중 진단비"
    assert policy_coverages.normalize_standard_name("뇌출혈진단비") == "뇌출혈 진단비"
    assert policy_coverages.normalize_standard_name("특정상해성뇌출혈진단비") == "기타"
    assert policy_coverages.normalize_standard_name("[건강]상급종합병원 1인실 입원일당") == "1인실 입원일당"
    assert policy_coverages.normalize_standard_name("다빈치로봇 암수술비") == "암수술비"
    assert policy_coverages.normalize_standard_name("일반상해종합병원입원일당") == "상해일당"
    assert policy_coverages.normalize_standard_name("질병수술입원일당") == "질병일당"
    assert policy_coverages.normalize_standard_name("중증화상및부식진단비", "기타") == "기타"
    assert policy_coverages.normalize_standard_name("10대특정암진단비", "특정암 진단비") == "특정암 진단비"
    assert policy_coverages.normalize_standard_name("통합암진단비", "통합암 진단비") == "통합암 진단비"


def test_source_contract_identity_distinguishes_kb_life_from_kb_insurance():
    assert policy_coverages._same_source_insurer("라이나생명", "라이나생명보험")
    assert policy_coverages._same_source_insurer("KB손보", "KB손해보험")
    assert not policy_coverages._same_source_insurer("KB라이프생명", "KB손보")
    assert not policy_coverages._same_source_rider(
        "KB 경영인정기보험Ⅱ 무배당(해약환급금 일부지급형)",
        "KB 플러스 운전자상해보험(무배당)(25.04)",
    )


def test_appendix_header_identity_is_structural_for_unseen_insurers_and_ambiguous_headers_fail_closed():
    insurer_names = [
        "KDB생명", "AIG손보", "AIA생명", "NH농협손보", "롯데손보", "AXA손보",
        "에이스손보", "우정사업본부", "신한생명", "KB라이프생명", "DB생명", "미래새보험",
    ]
    for insurer in insurer_names:
        rows = [
            ["별첨", "상품별 보험가입현황", "1"], [], [insurer], ["새 상품 1종"],
            ["계약자/피보험자", "합성고객"], ["보험기간", "2031.01.01~2041.01.01"],
            ["NO", "구분", "회사 담보명", "신정원 담보명", "가입금액"],
        ]
        state, identity = policy_coverages._appendix_header_policy(rows, 6)
        assert state == "explicit"
        assert identity == {
            "insurer": insurer, "product_name": "새 상품 1종",
            "start_date": "2031-01-01", "end_date": "2041-01-01",
        }

    ambiguous = [
        ["별첨", "상품별 보험가입현황", "2"], [], ["보험사만있음"],
        ["가입담보명 및 가입금액"], ["NO", "구분", "회사 담보명", "가입금액"],
    ]
    state, identity = policy_coverages._appendix_header_policy(ambiguous, 4)
    assert state == "ambiguous"
    assert identity == {}

    continuation = [
        ["별첨", "상품별 보험가입현황", "3"], [], ["가입담보명 및 가입금액"],
        ["NO", "구분", "회사 담보명", "가입금액"],
    ]
    state, identity = policy_coverages._appendix_header_policy(continuation, 3)
    assert state == "continuation"
    assert identity == {}


def test_appendix_continuation_requires_adjacent_sequential_rider_numbers():
    assert policy_coverages._can_carry_appendix_identity(10, 11, 30, 31)
    assert not policy_coverages._can_carry_appendix_identity(10, 12, 30, 31)
    assert not policy_coverages._can_carry_appendix_identity(10, 11, 30, 1)
    assert not policy_coverages._can_carry_appendix_identity(10, 11, None, 31)


def test_credit_bureau_detail_table_is_contract_ledger_without_summary_double_count(monkeypatch):
    table_rows = [
        ["신정원담보명", "회사명", "상품명", "회사담보명", "가입금액", "보험시기", "보험종기"],
        ["암진단", "합성손해보험", "합성 건강보험", "일반암 진단비", "3,000만", "2025.01.01", "2045.01.01"],
        ["질병수술", "", "", "질병 수술비", "0", "2025.01.01", "2045.01.01"],
        ["기타", "합성생명", "합성 종신보험", "금액 확인 담보", "-", "2024.02.02", "2054.02.02"],
        [None, None, None, None, None, None, None],
    ]

    class FakeTable:
        def extract(self):
            return table_rows

    class FakePage:
        def find_tables(self):
            return [FakeTable()]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _path: FakePdf())
    rows, occurrences = policy_coverages.parse_policy_coverages_from_pdf(
        "synthetic-kb.pdf", "synthetic-kb.pdf", "synthetic-hash", with_occurrences=True
    )

    assert len(rows) == 3
    assert occurrences == []
    assert [(row["insurer"], row["product_name"], row["rider_name"]) for row in rows] == [
        ("합성손해보험", "합성 건강보험", "일반암 진단비"),
        ("합성손해보험", "합성 건강보험", "질병 수술비"),
        ("합성생명", "합성 종신보험", "금액 확인 담보"),
    ]
    assert [(row["amount"], row["amount_status"]) for row in rows] == [
        (30_000_000, "known"),
        (0, "explicit_zero"),
        (None, "unknown_dash"),
    ]
    assert [row["source_page"] for row in rows] == [1, 1, 1]
    assert len({row["source_occurrence_key"] for row in rows}) == 3
    assert all(row["source_type"] == "contract_ledger" for row in rows)


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
    assert next(r for r in rows if r["rider_name"] == "[건강]뇌혈관질환 진단비")["rider_no"] == "4"
    assert ("[건강]허혈성심장질환 진단비", "허혈성심장질환 진단비", "3,000만", 28) in names
    assert ("[건강]질병 1~5종 수술비(5종)", "질병종수술", "1,000만", 28) in names
    assert all(r["coverage_name"] == r["rider_name"] for r in rows)
    assert all(r["current_amount"] == r["amount_text"] for r in rows)
    assert all(r["product_name"].startswith("무배당 삼성화재 건강보험 New내돈내삼") for r in rows)


def test_policy_coverages_appendix_accepts_eok_and_wins_over_duplicate_detail_list():
    detail_page = """
가입담보 상세 List
메리츠화재 일반상해사망 1억 2025-02-14 2081-02-14
"""
    appendix_page = """
별첨 상품별 보험가입현황 30
메리츠화재
(무) 메리츠 The건강한 내Mom대로 5.10.5 보장보험2501
보험기간 2025.02.14~2081.02.14 월 보험료 115,500원
가입담보명 및 가입금액 (단위 : 원)
NO 구분 회사 담보명 신정원 담보명 가입금액
1 정액 일반상해사망(건강가입)[기본계약] 상해사망 1억
"""
    rows = policy_coverages.parse_policy_coverages_from_pages(
        [(21, detail_page), (31, appendix_page)], "sample.pdf", "hash"
    )
    assert len(rows) == 1
    assert rows[0]["source_page"] == 31
    assert rows[0]["insurer"] == "메리츠화재"
    assert rows[0]["product_name"].startswith("(무) 메리츠 The건강한")
    assert rows[0]["rider_name"] == "일반상해사망(건강가입)[기본계약]"
    assert rows[0]["standard_name"] == "상해사망"
    assert rows[0]["standard_name_raw"] == "상해사망"
    assert rows[0]["rider_name_raw"] == "일반상해사망(건강가입)[기본계약]"
    assert rows[0]["source_type"] == "contract_ledger"
    assert rows[0]["amount"] == 100_000_000


def test_policy_coverages_appendix_keeps_composite_man_cheon_and_standalone_cheon_rows():
    page = """
별첨 상품별 보험가입현황 30
메리츠화재
(무) 자동차보장보험
보험기간 2025.01.01~2035.01.01 월 보험료 10,000원
가입담보명 및 가입금액 (단위 : 원)
NO 구분 회사 담보명 신정원 담보명 가입금액
1 정액 특정질병수술급여금 기타 384만6천
2 실손 다른 자동차 운전담보 기타 1천
"""
    rows = policy_coverages.parse_policy_coverages_from_pages([(30, page)], "sample.pdf", "hash")
    assert [(row["rider_name"], row["amount"]) for row in rows] == [
        ("특정질병수술급여금", 3_846_000),
        ("다른 자동차 운전담보", 10_000_000),
    ]


def test_policy_coverages_diagnosis_detail_parser_handles_single_line_layout():
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
    rows = policy_coverages.parse_policy_coverages_from_pages([(5, page)], "summary.pdf", "hash")
    assert len(rows) == 4
    by_name = {r["rider_name"]: r for r in rows}
    assert by_name["일반암진단비"]["amount"] == 160_000_000
    assert by_name["질병 3%이상후유장해"]["amount_text"] == "3,000만"
    assert all(r["insurer"] == "통합보장분석" and r["product_name"] == "M·보장분석" for r in rows)


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
    assert by_name["질병사망"]["amount"] is None
    assert by_name["질병사망"]["amount_text"] == "-"
    assert by_name["질병사망"]["amount_status"] == "unknown_dash"
    assert by_name["질병입원일당"]["standard_name"] == "질병일당"
    assert all(r["insurer"] == "알 수 없음" and r["product_name"] == "보장분석 요약" for r in rows)


def test_policy_coverages_bulk_requires_review_and_links_policy(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "테스트고객"})
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


def test_policy_coverages_bulk_is_idempotent_for_same_source(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "테스트고객"})
    item = {
        "source_hash": "same-document-hash",
        "source_page": 7,
        "insurer": "테스트손보",
        "product_name": "테스트보험",
        "rider_no": "3",
        "rider_name": "질병사망",
        "standard_name": "질병사망",
        "amount": 10_000_000,
    }
    first = policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    second = policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    assert first["created"] == 1
    assert first["skipped_existing"] == 0
    assert second["created"] == 0
    assert second["skipped_existing"] == 1
    assert len(policy_coverages.list_policy_coverages(conn, customer["id"])) == 1


def test_policy_coverages_bulk_rejects_other_customer_policy_atomically(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer_a = repo.create_customer(conn, {"name": "고객A"})
    customer_b = repo.create_customer(conn, {"name": "고객B"})
    policy_b = repo.create_policy(conn, customer_b["id"], {"insurer": "타인보험", "product_name": "타인계약"})
    items = [
        {"rider_name": "정상담보", "standard_name": "질병사망", "amount": 1_000_000},
        {"policy_id": policy_b["id"], "rider_name": "교차담보", "standard_name": "상해사망", "amount": 2_000_000},
    ]
    try:
        policy_coverages.create_many(conn, customer_a["id"], items, require_review=True)
        assert False, "다른 고객의 policy_id는 거부해야 한다"
    except ValueError as exc:
        assert "다른 고객" in str(exc)
    assert policy_coverages.list_policy_coverages(conn, customer_a["id"]) == []


def test_policy_coverages_bulk_rejects_integrated_summary_as_contract_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "집계표고객"})
    item = {
        "insurer": "통합보장분석",
        "product_name": "M·보장분석",
        "rider_name": "질병사망",
        "standard_name": "질병사망",
        "amount": 10_000_000,
    }
    with pytest.raises(ValueError, match="집계표"):
        policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    assert policy_coverages.list_policy_coverages(conn, customer["id"]) == []


def test_coverage_document_separates_summary_rows_from_real_policies(monkeypatch):
    import importlib
    from database import intake

    router = importlib.import_module("database.router")

    summary = {
        "insurer": "통합보장분석", "product_name": "M·보장분석",
        "rider_name": "질병사망", "source_type": "coverage_analysis_full",
    }
    actual = {
        "insurer": "합성보험", "product_name": "합성건강보험",
        "rider_name": "일반암 진단비", "source_type": "coverage_analysis_full",
    }
    assert router._coverage_document_policies([summary], "보장별 상세 내역") == ([], [])

    mixed, warnings = router._coverage_document_policies([summary, actual], "보장별 상세 내역")
    assert warnings == []
    assert [(p["insurer"], p["product_name"]) for p in mixed] == [("합성보험", "합성건강보험")]

    monkeypatch.setattr(intake, "extract_policies_list", lambda _text: {
        "policies": [{"insurer": "목록보험", "product_name": "목록계약"}], "warnings": []
    })
    listed, warnings = router._coverage_document_policies([summary], "보유계약리스트\n목록보험 목록계약")
    assert warnings == []
    assert listed == [{"insurer": "목록보험", "product_name": "목록계약"}]


class _FakeCrop:
    def __init__(self, words):
        self.words = words

    def extract_words(self, **_kwargs):
        return self.words


class _FakePage:
    def __init__(self, chars, words):
        self.chars = chars
        self.words = words

    def crop(self, _bbox):
        return _FakeCrop(self.words)


class _FakeRow:
    def __init__(self, bbox):
        self.cells = [None, bbox]


class _FakeTable:
    def __init__(self, bbox):
        self.rows = [_FakeRow(bbox)]


def _fake_char(text, x0, adv=9.0):
    return {"text": text, "x0": x0, "x1": x0 + adv, "top": 0.0, "bottom": 10.0, "adv": adv, "width": adv}


def test_standard_cell_geometry_separates_short_overflow_from_verified_column_text():
    bbox = (100.0, 0.0, 200.0, 20.0)
    words = [
        {"text": "가입)", "x0": 100.0, "x1": 121.0},
        {"text": "기타", "x0": 102.0, "x1": 119.2},
    ]
    page = _FakePage([_fake_char("기", 102.0), _fake_char("타", 110.2)], words)
    text, status = policy_coverages._verified_standard_cell_text(page, _FakeTable(bbox), 0, 1, 2.0, 0.91)
    assert (text, status) == ("기타", "geometry_word")


def test_standard_cell_geometry_traces_column_stream_when_words_are_interleaved():
    bbox = (100.0, 0.0, 200.0, 20.0)
    words = [{"text": "입)기타", "x0": 100.0, "x1": 119.2}]
    chars = [
        _fake_char("입", 100.0, 4.0), _fake_char(")", 101.5, 3.0),
        _fake_char("기", 102.0), _fake_char("타", 110.2),
    ]
    page = _FakePage(chars, words)
    text, status = policy_coverages._verified_standard_cell_text(page, _FakeTable(bbox), 0, 1, 2.0, 0.91)
    assert (text, status) == ("기타", "geometry_trace")


def test_standard_cell_geometry_fails_closed_for_long_competing_stream():
    bbox = (100.0, 0.0, 200.0, 20.0)
    words = [
        {"text": "긴회사담보연속문자", "x0": 100.0, "x1": 150.0},
        {"text": "기타", "x0": 102.0, "x1": 119.2},
    ]
    page = _FakePage([_fake_char("기", 102.0), _fake_char("타", 110.2)], words)
    assert policy_coverages._verified_standard_cell_text(page, _FakeTable(bbox), 0, 1, 2.0, 0.91) == (None, "needs_review")


def test_policy_coverages_persists_extracted_verified_and_normalized_names_separately(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "원문분리고객"})
    item = {
        "rider_name": "회사담보",
        "standard_name_extracted_raw": "가1입인)실입원일당",
        "standard_name_raw": "1인실입원일당",
        "standard_name": "1인실 입원일당",
        "standard_name_verification": "geometry_word",
        "amount": 1_000_000,
    }
    policy_coverages.create_many(conn, customer["id"], [item], require_review=True)
    saved = policy_coverages.list_policy_coverages(conn, customer["id"])[0]
    assert saved["standard_name_extracted_raw"] == "가1입인)실입원일당"
    assert saved["standard_name_raw"] == "1인실입원일당"
    assert saved["standard_name"] == "1인실 입원일당"
    assert saved["standard_name_verification"] == "geometry_word"


def test_source_occurrences_are_idempotent_auditable_and_customer_isolated(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "검수고객"})
    other = repo.create_customer(conn, {"name": "다른고객"})
    item = {
        "source_hash": "doc-hash",
        "source_occurrence_key": "ledger:doc-hash:31:0:20",
        "source_page": 31,
        "insurer": "보험사",
        "product_name": "보험상품",
        "rider_name": "회사담보",
        "standard_name": "기타",
        "standard_name_extracted_raw": "섞인셀원문",
        "standard_name_verification": "needs_review",
        "review_status": "needs_review",
        "amount": 1_000_000,
    }
    occurrence = {
        "occurrence_key": "link:doc-hash:20:1:4",
        "source_hash": "doc-hash",
        "source_file_name": "local.pdf",
        "source_page": 20,
        "source_table": 1,
        "source_row": 4,
        "source_standard_name_raw": "질병수술",
        "source_standard_name": "질병수술비",
        "rider_name": "원본 상세담보",
        "amount": 1_000_000,
        "amount_text": "100만",
        "resolution_status": "unresolved",
        "resolution_reason": "candidate_multiple",
        "candidate_source_keys_json": '["ledger:doc-hash:31:0:20"]',
    }
    first = policy_coverages.create_many(conn, customer["id"], [item], source_occurrences=[occurrence])
    second = policy_coverages.create_many(conn, customer["id"], [item], source_occurrences=[occurrence])
    assert first["source_occurrences"] == {"created": 1, "updated": 0, "total": 1}
    assert second["source_occurrences"] == {"created": 0, "updated": 1, "total": 1}
    rows = policy_coverages.list_source_occurrences(conn, customer["id"])
    assert len(rows) == 1
    assert rows[0]["candidate_policy_coverage_ids"] == [first["items"][0]["id"]]
    assert policy_coverages.list_source_occurrences(conn, other["id"]) == []
    noncandidate = policy_coverages.create_many(
        conn,
        customer["id"],
        [{**item, "source_occurrence_key": "ledger:doc-hash:31:0:21", "rider_name": "비후보"}],
    )["items"][0]
    cross_source = policy_coverages.create_many(
        conn,
        customer["id"],
        [{**item, "source_hash": "other-hash", "source_occurrence_key": "ledger:other-hash:31:0:22", "rider_name": "타문서"}],
    )["items"][0]
    with pytest.raises(ValueError, match="후보가 아닙니다"):
        policy_coverages.review_source_occurrence(
            conn, customer["id"], rows[0]["id"], "link", noncandidate["id"]
        )
    with pytest.raises(ValueError, match="후보가 아닙니다"):
        policy_coverages.review_source_occurrence(
            conn, customer["id"], rows[0]["id"], "link", cross_source["id"]
        )
    assert policy_coverages.list_source_occurrences(conn, customer["id"])[0]["resolution_status"] == "unresolved"
    with pytest.raises(ValueError, match="다른 고객"):
        policy_coverages.review_source_occurrence(
            conn, customer["id"], rows[0]["id"], "link",
            policy_coverages.create_many(conn, other["id"], [{"rider_name": "타인담보", "amount": 1}])["items"][0]["id"],
        )
    reviewed = policy_coverages.review_source_occurrence(
        conn, customer["id"], rows[0]["id"], "link", first["items"][0]["id"], "원본 확인"
    )
    assert reviewed["resolution_status"] == "confirmed"
    assert reviewed["resolution_reason"] == "user_confirmed"
    saved = policy_coverages.get_policy_coverage(conn, first["items"][0]["id"])
    assert saved["source_link_status"] == "user_confirmed"
    assert json.loads(saved["source_standard_names_json"]) == ["질병수술비"]
    with pytest.raises(ValueError, match="이미 검수"):
        policy_coverages.review_source_occurrence(
            conn, customer["id"], rows[0]["id"], "link", first["items"][0]["id"]
        )


def test_analysis_lists_every_needs_review_row_with_raw_provenance_even_when_unlinked(tmp_path, monkeypatch):
    db_path = tmp_path / "customers.sqlite3"
    monkeypatch.setenv("CUSTOMER_DB_PATH", str(db_path))
    conn = connect(db_path)
    init_schema(conn)
    customer = repo.create_customer(conn, {"name": "31쪽검수"})
    items = [
        {
            "source_hash": "page31-hash",
            "source_occurrence_key": f"ledger:page31-hash:31:0:{row_no}",
            "source_page": 31,
            "rider_no": str(row_no),
            "rider_name": f"회사담보{row_no}",
            "standard_name_extracted_raw": f"섞인추출{row_no}",
            "standard_name_raw": None,
            "standard_name": "암 진단비" if row_no in (20, 24) else "기타",
            "standard_name_verification": "needs_review",
            "review_status": "needs_review",
            "source_link_status": "none",
            "source_standard_name": "암 진단비" if row_no in (20, 24) else None,
            "source_standard_names_json": '["암 진단비"]' if row_no in (20, 24) else "[]",
            "amount": row_no * 10_000,
        }
        for row_no in (20, 24, 25, 27, 31)
    ]
    policy_coverages.create_many(conn, customer["id"], items)
    recalculated = coverage.recalculate(conn, customer["id"], {"force": True, "persist": True})
    result = coverage.get_analysis_response(conn, customer["id"], run_id=recalculated["run_id"])
    assert len(result["review_required_policy_coverages"]) == 5
    assert len(result["unlinked_policy_coverages"]) == 5
    assert {row["id"] for row in result["unlinked_policy_coverages"]} == {
        row["id"] for row in result["review_required_policy_coverages"]
    }
    assert {row["source_page"] for row in result["review_required_policy_coverages"]} == {31}
    assert {row["standard_name_extracted_raw"] for row in result["review_required_policy_coverages"]} == {
        "섞인추출20", "섞인추출24", "섞인추출25", "섞인추출27", "섞인추출31"
    }
    assert all(row["standard_name_verification"] == "needs_review" for row in result["review_required_policy_coverages"])
