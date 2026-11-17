"""Synthetic PDFs only: exercise the real capture parser, not a generic LLM parser."""
import importlib
import pytest


def synthetic_pdf(coverage_table=False, empty=False, zero_amount=False):
    # Built-in Korean CID font + vector table: no external documents/fonts/tools.
    commands=[]
    def text(x,y,value):
        commands.append(f'BT /F1 9 Tf {x} {y} Td <{value.encode("utf-16-be").hex()}> Tj ET')
    if coverage_table:
        text(30,790,'합성 보장분석 보유계약리스트')
        text(30,770,'가입 담보 없음' if empty else '합성 원장')
        if not empty:
            rows=[['신정원담보명','회사명','상품명','회사담보명','가입금액','보험시기','보험종기'],['일반암','합성보험','합성상품','암진단비','1,000만','2030.01.01','2040.01.01'],['질병입원일당','합성보험','합성상품','질병입원일당','3만','2030.01.01','2040.01.01']]
            if zero_amount:
                for row in rows[1:]: row[4]='0원'
            for col in range(8):commands.append(f'{20+col*80} 650 m {20+col*80} 740 l S')
            for row in range(4):commands.append(f'20 {740-row*30} m 580 {740-row*30} l S')
            for ri,row in enumerate(rows):
                for ci,value in enumerate(row):text(22+ci*80,720-ri*30,value)
    else:
        for i,line in enumerate(['Insurance policy summary','Customer: QA-ZERO-001','Insurer: Synthetic Insurance','Product: Synthetic Policy','Cancer diagnosis benefit: 10000000 KRW','Hospital daily benefit: 30000 KRW','Monthly premium: 50000 KRW']):text(30,790-i*20,line)
    stream='\n'.join(commands).encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 620 840] /Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>',b'<< /Type /Font /Subtype /Type0 /BaseFont /HYSMyeongJo-Medium /Encoding /UniKS-UCS2-H /DescendantFonts [5 0 R] >>',b'<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HYSMyeongJo-Medium /CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) /Supplement 1 >> /DW 1000 >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    result=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(result));result+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(result);result+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    result+=b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return result+f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


def extraction_seams(monkeypatch):
    router=importlib.import_module('database.router')
    from database import intake
    # Existing optional LLM identity/policy fields are controlled, not called.
    monkeypatch.setattr(router,'extract_customer_fields',lambda *_:{'fields':{'name':'QA-ZERO-001'},'warnings':[]})
    monkeypatch.setattr(intake,'extract_policy_fields',lambda *_:{'policy':{'insurer':'합성보험','product_name':'합성상품','premium_won':50000},'warnings':[]})
    def denied(*a,**kw):raise AssertionError('LLM is forbidden in this regression')
    monkeypatch.setattr(intake,'extract_policies_list',lambda *_:{'policies':[],'warnings':[]})
    monkeypatch.setattr(intake,'_call_llm',denied)
    return router


def test_unrecognized_pdf_with_two_text_benefits_is_not_completed_coverage(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    extraction_seams(monkeypatch)
    response=TestClient(app).post('/capture',files={'file':('synthetic.pdf',synthetic_pdf(),'application/pdf')})
    assert response.status_code==200
    row=response.json()['items'][0]
    assert len(row['policies'])==1 and row['policy_coverages']==[]
    assert row['analysis_status']=='coverage_format_unrecognized'
    assert any('자동 담보 추출' in warning for warning in row['warnings'])


def test_recognized_empty_pdf_stays_reviewable_not_unsupported(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    extraction_seams(monkeypatch)
    response=TestClient(app).post('/capture',files={'file':('empty.pdf',synthetic_pdf(coverage_table=True,empty=True),'application/pdf')})
    assert response.status_code==200
    row=response.json()['items'][0]
    assert row['policy_coverages']==[]
    assert row['analysis_status']=='coverage_review_required'
    assert any('담보가 없는 정상 문서' in warning for warning in row['warnings'])
    assert not any('지원하는 양식으로 인식되지' in warning for warning in row['warnings'])


@pytest.mark.parametrize('zero_amount',[False,True])
def test_supported_credit_table_rows_reach_capture_without_llm(monkeypatch,zero_amount):
    from fastapi.testclient import TestClient
    from main import app
    extraction_seams(monkeypatch)
    response=TestClient(app).post('/capture',files={'file':('supported.pdf',synthetic_pdf(coverage_table=True,zero_amount=zero_amount),'application/pdf')})
    assert response.status_code==200
    row=response.json()['items'][0]
    assert row['analysis_status']=='quick_analysis_done'
    assert len(row['policies'])==1
    assert sorted(r['amount'] for r in row['policy_coverages'])==([0,0] if zero_amount else [30000,10000000])
    assert not any('지원하는 양식으로 인식되지' in warning for warning in row['warnings'])

