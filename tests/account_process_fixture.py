"""Fresh-process fixture; never connects to a real account or installation."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
from fastapi.testclient import TestClient
import main
client=TestClient(main.app)
client.headers['X-Insurance-AI-Secret']=main.API_SECRET
client.headers['Authorization']='Bearer '+os.environ['FIXTURE_TOKEN']
if sys.argv[1]=='create':
    response=client.post('/customers',json={'name':os.environ['FIXTURE_MARKER']})
    assert response.status_code==201,response.text
    cid=response.json()['id']
    Path('fixture-customer-id.txt').write_text(cid)
    edited=client.patch(f'/customers/{cid}',json={'memo':os.environ['FIXTURE_MARKER']+'-edited'})
    assert edited.status_code==200,edited.text
    relative=client.post('/customers',json={'name':os.environ['FIXTURE_MARKER']+'-relative'})
    assert relative.status_code==201,relative.text
    linked=client.post(f'/customers/{cid}/family-relationships',json={'related_customer_id':relative.json()['id'],'relationship':'SPOUSE'})
    assert linked.status_code==201,linked.text
    policy=client.post(f'/customers/{cid}/policies',json={'product_name':os.environ['FIXTURE_MARKER']})
    assert policy.status_code==201,policy.text
    consultation=client.post(f'/customers/{cid}/consultations',json={'title':os.environ['FIXTURE_MARKER'],'content':os.environ['FIXTURE_MARKER']})
    assert consultation.status_code==201,consultation.text
    Path('fixture-policy-id.txt').write_text(policy.json()['id'])
    analysis=client.post(f'/customers/{cid}/coverage-analysis/recalculate')
    assert analysis.status_code==200,analysis.text
    Path('fixture-analysis-id.txt').write_text(analysis.json()['run_id'])
if sys.argv[1]=='reset-restore':
    # Replace only the OS port reservation: no operating listener is touched.
    # Account middleware, API secret, DB/key and reset/restore code are real.
    from contextlib import nullcontext
    import database.reset_router as reset_api
    reset_api.mobile_port_guard=nullcontext
    reset=client.post('/business/reset',json={'confirmation':'초기화','confirmed':True,'request_id':'c'*32})
    assert reset.status_code==200,reset.text
    emptied=client.get('/customers')
    assert emptied.status_code==200 and emptied.json()=={'customers':[]},emptied.text
    receipt=reset.json()
    foreign=client.post('/business/reset/restore',json={'confirmation':'복원','confirmed':True,'request_id':'d'*32,'backup_filename':'../foreign-account.enc'})
    assert foreign.status_code==422,foreign.text
    restored=client.post('/business/reset/restore',json={'confirmation':'복원','confirmed':True,'request_id':'e'*32,'backup_filename':receipt['backup_filename']})
    assert restored.status_code==200,restored.text
    assert restored.json()['restored']
    raw=(Path(os.environ['CUSTOMER_DB_PATH']).parent/'backups'/receipt['backup_filename']).read_text()
    assert raw.startswith('enc:v1:') and os.environ['FIXTURE_MARKER'] not in raw
# Exercise real PDF parsing, RAG indexing/storage/search; only the model is synthetic.
from types import SimpleNamespace
import rag.pipeline
rag.pipeline.get_embedder=lambda:SimpleNamespace(name='fixture',embed=lambda texts:[[1.0,0.0] for _ in texts])
if sys.argv[1] in ('create','reset-restore'):
    marker=os.environ['FIXTURE_MARKER']
    stream=('BT /F1 12 Tf 20 700 Td ('+(marker+' ')*8+') Tj ET').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
             b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pdf=b'%PDF-1.4\n';offsets=[]
    for number,obj in enumerate(objects,1):
        offsets.append(len(pdf));pdf+=str(number).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(pdf);pdf+=b'xref\n0 6\n0000000000 65535 f \n'
    pdf+=b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    pdf+=f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    parsed=client.post('/parse/pdf',files={'file':(marker+'.pdf',pdf,'application/pdf')})
    assert parsed.status_code==200,parsed.text
    assert marker in parsed.text
    indexed=client.post('/rag/index',json=parsed.json())
    assert indexed.status_code==200,indexed.text
    Path('fixture-doc-id.txt').write_text(parsed.json()['doc_id'])
    attached=client.post(f"/policies/{Path('fixture-policy-id.txt').read_text()}/document",files={'file':(marker+'.pdf',pdf,'application/pdf')})
    assert attached.status_code==200,attached.text
    assert attached.json()['policy']['document_id']==parsed.json()['doc_id']
for path in ['/rag/docs','/rag/search?q=fixture']:
    rag_result=client.get(path)
    assert rag_result.status_code==200,rag_result.text
    assert os.environ['FIXTURE_MARKER'] in rag_result.text and os.environ['OTHER_MARKER'] not in rag_result.text,rag_result.text
cid=Path('fixture-customer-id.txt').read_text()
own_analysis=client.get(f'/customers/{cid}/coverage-analysis',params={'run_id':Path('fixture-analysis-id.txt').read_text()})
assert own_analysis.status_code==200,own_analysis.text
assert os.environ['OTHER_MARKER'] not in own_analysis.text
for suffix in ['policies','consultations']:
    own=client.get(f'/customers/{cid}/{suffix}')
    assert own.status_code==200 and os.environ['FIXTURE_MARKER'] in own.text,own.text
response=client.get('/customers')
assert response.status_code==200,response.text
assert os.environ['FIXTURE_MARKER'] in response.text,response.text
assert os.environ['OTHER_MARKER'] not in response.text,response.text
rejected=client.get('/customers',headers={'Authorization':'Bearer '+os.environ['OTHER_TOKEN'],'X-Actor-Id':os.environ['MOADOOM_ACCOUNT_SUBJECT']})
assert rejected.status_code==401,rejected.text
for path in ['/backups','/business/reset/status']:
    assert client.get(path,headers={'Authorization':'Bearer '+os.environ['OTHER_TOKEN']}).status_code==401
other_id_file=Path(os.environ['FIXTURE_OTHER_ID_FILE'])
if other_id_file.exists():
    other_id=other_id_file.read_text()
    for path in [f'/customers/{other_id}',f'/customers/{other_id}/ask?q=fixture',f'/customers/{other_id}/policies',f'/customers/{other_id}/consultations',f'/customers/{other_id}/policy-coverages',f'/customers/{other_id}/family-relationships',f'/customers/{other_id}/coverage-analysis']:
        # Current account is genuinely authenticated; the foreign resource must be absent.
        denied=client.get(path)
        assert denied.status_code==404,(path,denied.text)
        assert os.environ['OTHER_MARKER'] not in denied.text
    foreign_doc=(other_id_file.parent/'fixture-doc-id.txt').read_text()
    no_chunks=client.get('/rag/search',params={'q':'fixture','doc_id':foreign_doc})
    assert no_chunks.status_code==200 and os.environ['OTHER_MARKER'] not in no_chunks.text,no_chunks.text
    foreign_policy=(other_id_file.parent/'fixture-policy-id.txt').read_text()
    denied=client.post(f'/policies/{foreign_policy}/document',files={'file':('fixture.pdf',b'%PDF-1.4','application/pdf')})
    assert denied.status_code==404,denied.text
for path in ['/parse/pdf','/rag/search','/coverage-analysis-runs','/customers/foreign/ask?q=fixture']:
    denied=client.request('POST' if path=='/parse/pdf' else 'GET',path,headers={'Authorization':'Bearer '+os.environ['OTHER_TOKEN']})
    assert denied.status_code==401,(path,denied.text)
# Exercise ordinary backup/restore through HTTP, not only its library helper.
if sys.argv[1]=='create':
    snapshot=client.post('/maintenance/backup')
    assert snapshot.status_code==200 and snapshot.json()['created'],snapshot.text
    restored=client.post('/backups/restore',json={'filename':snapshot.json()['filename']})
    assert restored.status_code==200 and restored.json()['restart_required'],restored.text
import zipfile
exported=client.get('/export');assert exported.status_code==200,exported.text
export_path=Path(exported.json()['path']).resolve()
assert export_path.is_relative_to(Path(os.environ['CUSTOMER_DB_PATH']).parent.resolve())
with zipfile.ZipFile(export_path) as archive:
    text='\n'.join(archive.read(n).decode('utf-8-sig') for n in archive.namelist())
    assert os.environ['FIXTURE_MARKER'] in text and os.environ['OTHER_MARKER'] not in text
family=client.get(f'/customers/{cid}/family-relationships')
assert family.status_code==200 and len(family.json()['family_relationships'])==1,family.text
assert os.environ['OTHER_MARKER'] not in family.text
search=client.get('/customers',params={'q':os.environ['OTHER_MARKER']})
assert search.status_code==200 and search.json()=={'customers':[]},search.text
assert len(response.json()['customers'])==2,response.text
# No synthetic login transport in these process fixtures: launcher binding is explicit.
# Preserve real retrieval; replace only nondeterministic LLM inference.
from rag import answerer
contexts=[]
def fixture_llm(model,question,context):
    contexts.append(str(context))
    assert os.environ['OTHER_MARKER'] not in str(context)
    return {'answer':'synthetic answer','grounded':True,'company':None,'product':None,'clause':None}
answerer._call_llm=fixture_llm
answer=client.get(f'/customers/{cid}/ask',params={'q':os.environ['FIXTURE_MARKER']})
assert answer.status_code==200,answer.text
assert contexts and os.environ['FIXTURE_MARKER'] in '\n'.join(contexts),answer.text
assert os.environ['OTHER_MARKER'] not in answer.text
# Verify missing bearer and missing engine-secret independently, without the opt-in pytest plugin.
for header in ['Authorization','X-Insurance-AI-Secret']:
    value=client.headers.pop(header)
    for route in ['/customers','/export','/backups','/rag/docs',f'/customers/{cid}/family-relationships']:
        denied=client.get(route)
        assert denied.status_code==401,(header,route,denied.text)
        assert os.environ['FIXTURE_MARKER'] not in denied.text
    client.headers[header]=value
print(json.dumps({'ok':True,'count':len(response.json()['customers'])}))
