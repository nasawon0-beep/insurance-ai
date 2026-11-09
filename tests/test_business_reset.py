import base64, json, sys, sqlite3
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'local-engine'))
from database import db,repo,crypto
from lan_sync.store import SyncStore
from lan_sync.bridge import ProductionBridge

@pytest.fixture
def business(tmp_path,monkeypatch):
    monkeypatch.setenv('CUSTOMER_DB_PATH',str(tmp_path/'synthetic.sqlite3'))
    monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(b'r'*32).decode())
    crypto.reset_cache()
    c=db.connect();db.init_schema(c)
    customer=repo.create_customer(c,{'name':'RESET-SYNTHETIC-PII','memo':'reset fixture'})
    repo.create_policy(c,customer['id'],{'insurer':'fixture'})
    repo.create_consultation(c,customer['id'],{'consulted_at':'2026-01-01','content':'fixture consultation'})
    s=SyncStore(tmp_path/'synthetic.sqlite3');s.initialize();b=ProductionBridge(s);b.initialize();b.refresh()
    code=s.create_pairing_code('fixture');phone=s.pair_device(code.code,'fixture-phone','192.0.2.1')
    c.execute("INSERT INTO app_settings VALUES('retained-setting','yes','now')");c.commit()
    (tmp_path/'original.pdf').write_bytes(b'%PDF-synthetic');(tmp_path/'old-backup.enc').write_bytes(b'old-synthetic')
    yield c,s,b,phone,tmp_path
    s.close();c.close();crypto.reset_cache()

def service():
    from database.business_reset import reset_business_data
    return reset_business_data

def test_reset_is_atomic_business_only_encrypted_and_idempotent(business):
    c,s,b,phone,path=business
    before=[tuple(r) for r in c.execute('SELECT * FROM sync_devices')]
    result=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    assert result['sync_paused'] is True
    for table in ['customers','policies','consultations','sync_entities','sync_changes','sync_operations']:
        assert c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
    assert [tuple(r) for r in c.execute('SELECT * FROM sync_devices')]==before
    assert s.authenticate(phone.token).device_id==phone.device_id
    assert c.execute("SELECT value FROM app_settings WHERE key='retained-setting'").fetchone()[0]=='yes'
    assert (path/'original.pdf').read_bytes()==b'%PDF-synthetic'
    assert (path/'old-backup.enc').read_bytes()==b'old-synthetic'
    backup=next((path/'backups').iterdir());raw=backup.read_text()
    assert raw.startswith('enc:v1:') and 'RESET-SYNTHETIC-PII' not in raw
    assert base64.b64decode(crypto.get_cipher().decrypt(raw)).startswith(b'SQLite format 3')
    # Retrying the same committed request must NOT erase newly entered business rows.
    repo.create_customer(c,{'name':'new synthetic'})
    again=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    assert again['replayed'] and c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
    assert len(list((path/'backups').iterdir()))==1

def test_activity_gate_prevents_reset_and_new_work_overlap():
    from database.reset_activity import ActivityGate, ResetBusy
    gate=ActivityGate()
    with gate.work():
        with pytest.raises(ResetBusy):
            with gate.reset():pass
    with gate.reset():
        with pytest.raises(ResetBusy):
            with gate.work():pass
        with pytest.raises(ResetBusy):
            with gate.reset():pass
    with gate.work():pass

def test_reset_api_requires_secret_confirmation_and_exclusive_gate(business,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from database.reset_router import router
    from contextlib import nullcontext,contextmanager
    import database.reset_router as reset_api
    monkeypatch.setattr(reset_api,'mobile_port_guard',nullcontext)
    from database.reset_activity import BusinessResetActivityMiddleware,REQUESTS
    c,s,b,phone,path=business
    app=FastAPI();app.state.api_secret='fixture-secret';app.state.api_secret_header='X-Insurance-AI-Secret'
    app.include_router(router);app.add_middleware(BusinessResetActivityMiddleware)
    client=TestClient(app)
    client.headers.pop('X-Insurance-AI-Secret',None)  # undo suite's automatic fixture authentication
    body={'confirmation':'초기화','confirmed':True,'request_id':'e'*32}
    assert client.post('/business/reset',json=body).status_code==401
    headers={'X-Insurance-AI-Secret':'fixture-secret'}
    assert client.post('/business/reset',json={**body,'confirmation':'틀림'},headers=headers).status_code==422
    with REQUESTS.work():
        assert client.post('/business/reset',json=body,headers=headers).status_code==409
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
    @contextmanager
    def listener_busy():
        raise OSError('synthetic existing LAN listener')
        yield
    monkeypatch.setattr(reset_api,'mobile_port_guard',listener_busy)
    assert client.post('/business/reset',json=body,headers=headers).status_code==409
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
    monkeypatch.setattr(reset_api,'mobile_port_guard',nullcontext)
    response=client.post('/business/reset',json=body,headers=headers)
    assert response.status_code==200,response.text
    assert client.get('/business/reset/status',headers=headers).json()['sync_paused']
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==0

def test_port_reservation_never_listens_and_closes_on_failure(monkeypatch):
    from database.reset_router import mobile_port_guard
    import database.reset_router as reset_api
    calls=[]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):calls.append('closed')
        def setsockopt(self,*args):calls.append('exclusive')
        def bind(self,address):calls.append(address)
    monkeypatch.setattr(reset_api.socket,'socket',lambda *args:Socket())
    with pytest.raises(RuntimeError):
        with mobile_port_guard():raise RuntimeError('synthetic interruption')
    assert ('0.0.0.0',18443) in calls and calls[-1]=='closed'

@pytest.mark.parametrize('text,confirmed',[('',True),(' 초기화',True),('초기화',False)])
def test_confirmation_refuses_without_writes(business,text,confirmed):
    c,s,b,phone,path=business
    with pytest.raises(ValueError):service()(c,confirmation=text,confirmed=confirmed,request_id='b'*32,backup_directory=path/'backups')
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
    assert not (path/'backups').exists()

def test_existing_backup_collision_is_never_removed(business,monkeypatch):
    import database.business_reset as reset
    c,s,b,phone,path=business
    monkeypatch.setattr(reset.os,'urandom',lambda n:b'x'*n)
    backup=path/'backups';backup.mkdir()
    original=backup/('business-before-reset-'+('f'*32)+'-'+(b'x'*8).hex()+'.enc')
    original.write_bytes(b'preserved-existing-backup')
    with pytest.raises(FileExistsError):service()(c,confirmation='초기화',confirmed=True,request_id='f'*32,backup_directory=backup)
    assert original.read_bytes()==b'preserved-existing-backup'
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1

def test_failure_rolls_back_hold_and_all_deletes_then_retry(business):
    c,s,b,phone,path=business
    before=c.serialize()
    def fail(table):
        if table=='customers':raise RuntimeError('synthetic failure')
    with pytest.raises(RuntimeError):service()(c,confirmation='초기화',confirmed=True,request_id='c'*32,backup_directory=path/'backups',fault_hook=fail)
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1
    from database.business_reset import sync_is_held
    assert not sync_is_held(c)
    assert s.pull(s.authenticate(phone.token),0).items
    service()(c,confirmation='초기화',confirmed=True,request_id='c'*32,backup_directory=path/'backups')
    assert sync_is_held(c)

def test_phone_auth_preserved_but_sync_is_held_after_restart(business):
    from fastapi.testclient import TestClient
    from lan_sync.app import create_app
    c,s,b,phone,path=business
    service()(c,confirmation='초기화',confirmed=True,request_id='d'*32,backup_directory=path/'backups')
    app=create_app(path/'synthetic.sqlite3')
    with TestClient(app,base_url='https://test') as client:
        assert client.post('/v1/sync',json={}).status_code==401
        headers={'Authorization':f'Bearer {phone.token}'}
        response=client.post('/v1/sync',headers=headers,json={'cursor':0,'operations':[]})
        assert response.status_code==409
        assert response.json()['detail']=='business_reset_sync_hold'
        assert client.post('/v1/continuity',headers=headers).status_code==200
    # Refresh after reset must not emit deletions to phones, including a stale pre-reset snapshot.
    b.refresh()
    assert c.execute('SELECT COUNT(*) FROM sync_changes').fetchone()[0]==0
    with pytest.raises(Exception,match='business_reset_sync_hold'):
        s.reconcile_snapshots({('customer','old'):{'name':'old synthetic'}},frozenset({'customer'}))
