from types import SimpleNamespace
from database import policy_coverages as pc


def test_contract_detail_unmatched_rows_are_preserved_without_double_sum(monkeypatch):
    table=SimpleNamespace(extract=lambda:[['1','정액','상해사망특약','상해사망','1억'],['2','정액','합성깁스치료특약','깁스치료','30만']])
    class PDF:
        pages=[SimpleNamespace(find_tables=lambda:[table],extract_text=lambda:'합성보험 합성상품 2030-01-01')]
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr('pdfplumber.open',lambda *_:PDF())
    contracts=[{'insurer':'합성보험','product_name':'합성상품','issued_date':'2030-01-01'}]
    ledger=[{'insurer':'합성보험','product_name':'합성상품','rider_name_raw':'상해사망특약','amount_text':'1억','start_date':'2030-01-01'}]
    result=pc.parse_contract_detail_references_from_pdf('synthetic.pdf',contracts,ledger,source_hash='fixture')
    assert len(result)==1
    assert result[0]['rider_name']=='합성깁스치료특약'
    assert result[0]['amount']==300000
    assert result[0]['standard_name_raw']=='깁스치료'
    assert result[0]['document_status']=='REFERENCE'
    assert result[0]['review_status']=='needs_review'
    assert result[0]['source_page']==1 and result[0]['source_row']==1


def test_source_only_customer_identity_does_not_infer_from_filename_or_age(monkeypatch):
    import importlib
    router=importlib.import_module('database.router')
    def forbidden(*a,**kw):raise AssertionError('identity inference forbidden')
    monkeypatch.setattr(router,'_name_from_filename',forbidden)
    monkeypatch.setattr(router,'_estimate_birthdate_from_age',forbidden)
    fields,_=router._quick_customer_fields('보장분석 35세','가나다.pdf','2030-01-01',allow_inferred_identity=False)
    assert fields['name'] is None and fields['birth_date'] is None


def test_same_product_contract_tie_is_not_assigned_to_first_policy(monkeypatch):
    from database import repo
    candidates=[{'id':p,'insurer':'SYNTHETIC','product_name':'SYNTHETIC','start_date':'2030-01-01'} for p in ['a','b']]
    monkeypatch.setattr(repo,'list_policies',lambda *a:candidates)
    assert pc._match_policy_id(None,'customer',{'insurer':'SYNTHETIC','product_name':'SYNTHETIC','start_date':'2030-01-01'})==(None,'unmatched')
