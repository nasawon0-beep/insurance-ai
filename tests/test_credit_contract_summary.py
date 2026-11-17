"""Synthetic-only credit contract header regression."""
from types import SimpleNamespace
from database import policy_coverages as pc


def test_credit_contract_summary_keeps_dates_premium_and_payment_period(monkeypatch):
    rows=[['1','합성보험','같은상품','2030-01-01','월납','20년','90세','12,300원',''],
          ['2','합성보험','같은상품','2031-01-01','','0년','90세','보험료미제공','']]
    table=SimpleNamespace(extract=lambda:rows)
    class PDF:
        pages=[SimpleNamespace(find_tables=lambda:[table])]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('pdfplumber.open',lambda *_:PDF())
    result=pc.parse_credit_contracts_from_pdf('synthetic.pdf')
    assert len(result)==2
    assert [p['issued_date'] for p in result]==['2030-01-01','2031-01-01']
    assert result[0]['premium_won']==12300 and result[0]['payment_period']=='20년'
    assert result[0]['insured_period']=='90세'
    assert result[1]['premium_won'] is None and result[1]['payment_cycle'] is None
    assert result[1]['payment_period']=='0년'


def test_credit_summary_is_separate_from_riders_and_preserves_dash(monkeypatch):
    rows=[['사망','상해사망','1억 2,000만','2,000만','1억','-','-'],
          ['', '질병사망','-','-','-','-','-']]
    table=SimpleNamespace(extract=lambda:rows)
    class PDF:
        pages=[SimpleNamespace(find_tables=lambda:[table])]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('pdfplumber.open',lambda *_:PDF())
    result=pc.parse_credit_summaries_from_pdf('synthetic.pdf')
    assert len(result)==2
    assert result[0]['amount']==120000000
    assert result[0]['recommended_amount'] is None
    assert result[1]['amount'] is None and result[1]['amount_status']=='unknown_dash'
