"""Isolation acceptance fixtures, NOT real central-login / installed-app evidence."""
import hashlib,os,subprocess,sys
from pathlib import Path
from fastapi.testclient import TestClient
from account_workspace import VerifiedPrincipal,workspace_for
from lan_sync.app import create_app

A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ISSUER='https://fixture.supabase.co/auth/v1'

def test_actual_business_api_two_installations_a_b_a_and_restart(tmp_path):
    for installation in ['PC One 한글','PC Two spaces']:
        root=tmp_path/installation;root.mkdir()
        legacy=root/'customers.sqlite3';legacy.write_bytes(b'UNASSIGNED LEGACY FIXTURE')
        for subject,action in [(A,'create'),(B,'create'),(A,'reset-restore'),(B,'read'),(A,'read')]:
            other=B if subject==A else A
            layout=workspace_for(root,VerifiedPrincipal(ISSUER,subject))
            layout.database.parent.mkdir(parents=True,exist_ok=True);layout.key.parent.mkdir(parents=True,exist_ok=True)
            env=os.environ.copy();env.update({
              'DEV_SKIP_AUTH':'0','CUSTOMER_DB_PATH':str(layout.database),'RAG_DB_PATH':str(layout.rag_database),
              'CUSTOMER_DB_KEYFILE':str(layout.key),'CUSTOMER_DB_KEYFILE_ONLY':'1','CUSTOMER_DB_KEY_B64':'',
              'INSURANCE_AI_API_SECRET_FILE':str(layout.root/'fixture-api.secret'),
              'MOADOOM_ACCOUNT_ISSUER':ISSUER,'MOADOOM_ACCOUNT_SUBJECT':subject,
              'MOADOOM_ACCOUNT_TOKEN_SHA256':hashlib.sha256(subject.encode()).hexdigest(),'MOADOOM_ACCOUNT_EXPIRES_AT':'4102444800',
              'FIXTURE_TOKEN':subject,'OTHER_TOKEN':other,'FIXTURE_MARKER':'PRIVATE-'+subject,'OTHER_MARKER':'PRIVATE-'+other,
              'COVERAGE_ANALYSIS_LOG_DIR':str(layout.root/'logs'),
              'FIXTURE_OTHER_ID_FILE':str(workspace_for(root,VerifiedPrincipal(ISSUER,other)).root/'fixture-customer-id.txt'),
            })
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('account_process_fixture.py')),action],cwd=layout.root,env=env,capture_output=True,text=True,timeout=35)
            assert result.returncode==0,result.stdout+result.stderr
        assert legacy.read_bytes()==b'UNASSIGNED LEGACY FIXTURE'
        assert workspace_for(root,VerifiedPrincipal(ISSUER,A)).key.read_bytes()!=workspace_for(root,VerifiedPrincipal(ISSUER,B)).key.read_bytes()

def test_phone_token_from_a_cannot_read_b(tmp_path,monkeypatch):
    monkeypatch.delenv('MOADOOM_ACCOUNT_EXPIRES_AT',raising=False)
    apps=[create_app(tmp_path/(subject+'.sqlite3'),enable_production_bridge=False) for subject in [A,B]]
    clients=[TestClient(app,base_url='https://fixture') for app in apps]
    tokens=[]
    for app,client in zip(apps,clients):
        code=app.state.sync_store.create_pairing_code('Synthetic PC')
        paired=client.post('/v1/pair',json={'code':code.code,'device_name':'Synthetic phone'})
        assert paired.status_code==200,paired.text
        tokens.append(paired.json()['token'])
    for own,other in [(0,1),(1,0)]:
        assert clients[own].post('/v1/sync',headers={'Authorization':'Bearer '+tokens[other]},json={}).status_code==401
        assert clients[own].post('/v1/sync',headers={'Authorization':'Bearer '+tokens[own]},json={}).status_code==200

def test_expired_account_prevents_phone_pairing(tmp_path,monkeypatch):
    monkeypatch.setenv('MOADOOM_ACCOUNT_EXPIRES_AT','1')
    app=create_app(tmp_path/'expired.sqlite3',enable_production_bridge=False)
    code=app.state.sync_store.create_pairing_code('Synthetic PC')
    result=TestClient(app,base_url='https://fixture').post('/v1/pair',json={'code':code.code,'device_name':'Synthetic phone'})
    assert result.status_code==401,result.text
