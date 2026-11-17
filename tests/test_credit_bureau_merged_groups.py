"""Generic credit-bureau geometry regressions; no customer PDF/identity data."""
from types import SimpleNamespace
from database import policy_coverages as pc


def test_credit_bureau_merged_group_uses_physical_cell_span(monkeypatch):
    header=['신정원담보명','회사명','상품명','회사담보명','가입금액','보험시기','보험종기']
    rows=[header,['질병입원의료비','합성보험','합성상품','질병 전체','5,000만','2030.01.01','2040.01.01'],[None,'합성보험','합성상품2','질병 전체','3,000만','2030.01.01','2040.01.01'],['','합성보험','합성상품3','정액 수술비','30만','2030.01.01','2040.01.01']]
    cells=[[(i*10,0,(i+1)*10,10) for i in range(7)],[(0,10,10,30)]+[(i*10,10,(i+1)*10,20) for i in range(1,7)],[None]+[(i*10,20,(i+1)*10,30) for i in range(1,7)],[(i*10,30,(i+1)*10,40) for i in range(7)]]
    table=SimpleNamespace(extract=lambda:rows,rows=[SimpleNamespace(cells=c) for c in cells])
    class PDF:
        pages=[SimpleNamespace(find_tables=lambda:[table])]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('pdfplumber.open',lambda *_:PDF())
    result=pc.parse_policy_coverages_from_pdf('synthetic.pdf')
    assert len(result)==3
    assert result[1]['standard_name_raw']=='질병입원의료비'
    assert result[1]['standard_name']=='질병입원'
    assert result[1]['amount']==30000000
    assert result[2]['standard_name']!='질병입원'


def test_exact_source_labels_and_indemnity_do_not_become_fixed_daily_benefits():
    assert pc.normalize_standard_name('질병 전체','일반암')=='일반암 진단비'
    assert pc.normalize_standard_name('질병 전체','허혈성심장질 환')=='허혈성심장질환 진단비'
    assert pc.normalize_standard_name('의료 수술 정액급여')=='기타'
    assert pc.normalize_standard_name('질병수술비')=='질병수술비'
    assert pc.normalize_standard_name('질병입원일당')=='질병일당'
    assert pc.normalize_standard_name('특정 수술','기타')=='기타'
    assert pc._SHORT_FORM_STANDARD_NAMES['3대비급여실손']!='1인실 입원일당'


def test_combined_nursing_and_long_term_care_are_not_one_specific_daily_benefit():
    for label in ['간병인/간호간병상해일당', '간병인/간호간병질병일당', '장기요양간병비']:
        assert pc.normalize_standard_name('합성 담보', label) == label


def test_motor_contract_rows_preserve_unknown_amount_without_vehicle_identifier(monkeypatch):
    rows=[['회사명','상품명','차량번호','담보명','보험기간','가입금액'],
          ['합성보험','합성자동차보험','SYNTHETIC-VEHICLE','대인배상II','2030.01.01 ~ 2031.01.01','무한'],
          [None,None,None,None,None,None]]
    table=SimpleNamespace(extract=lambda:rows)
    class PDF:
        pages=[SimpleNamespace(find_tables=lambda:[table],extract_text=lambda:'자동차보험 가입현황')]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('pdfplumber.open',lambda *_:PDF())
    result=pc.parse_policy_coverages_from_pdf('synthetic.pdf',source_hash='fixture')
    assert len(result)==1
    row=result[0]
    assert row['rider_name']=='대인배상II'
    # Existing schema uses parse_failed for non-numeric limits; preserve text,
    # NULL amount and needs_review rather than introducing an unsupported enum.
    assert row['amount'] is None and row['amount_status']=='parse_failed'
    assert row['amount_text']=='무한'
    assert row['start_date']=='2030-01-01' and row['end_date']=='2031-01-01'
    assert row['review_status']=='needs_review'
    assert row['standard_name']=='자동차보험'
    assert 'SYNTHETIC-VEHICLE' not in str(row)
