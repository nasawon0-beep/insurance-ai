"""Synthetic-only reset recovery, never connects to operating DB/ports."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_business_reset import business,service
from database import repo
import database.business_reset as reset


def test_restore_recovers_original_sync_and_preserves_current_auth(business):
    c,s,b,phone,path=business
    original=[tuple(r) for r in c.execute('SELECT * FROM customers')]
    changes=[tuple(r) for r in c.execute('SELECT * FROM sync_changes')]
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    repo.create_customer(c,{'name':'POST-RESET-NEW-WORK'})
    c.execute("UPDATE app_settings SET value='changed' WHERE key='retained-setting'");c.commit()
    devices=[tuple(r) for r in c.execute('SELECT * FROM sync_devices')]
    restore=getattr(reset,'restore_business_data',None)
    assert callable(restore),'Encrypted reset recovery is missing'
    result=restore(c,confirmation='복원',confirmed=True,request_id='b'*32,
        backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    assert result['restored'] and not result['sync_paused']
    assert [tuple(r) for r in c.execute('SELECT * FROM customers')]==original
    assert [tuple(r) for r in c.execute('SELECT * FROM sync_changes')]==changes
    assert [tuple(r) for r in c.execute('SELECT * FROM sync_devices')]==devices
    assert c.execute("SELECT value FROM app_settings WHERE key='retained-setting'").fetchone()[0]=='changed'
    assert s.authenticate(phone.token).device_id==phone.device_id
    assert s.pull(s.authenticate(phone.token),0).items
    assert (path/'backups'/result['safety_copy']).read_text().startswith('enc:v1:')
    # Lost response retry must not overwrite work created after successful restore.
    repo.create_customer(c,{'name':'AFTER-RESTORE'})
    retry=restore(c,confirmation='복원',confirmed=True,request_id='b'*32,
        backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    assert retry['replayed']
    assert c.execute('SELECT count(*) FROM customers').fetchone()[0]==2


def test_restore_api_status_auth_exclusive_gate_and_stopped_listener(business,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from contextlib import nullcontext,contextmanager
    from database.reset_activity import BusinessResetActivityMiddleware,REQUESTS
    import database.reset_router as api
    monkeypatch.setattr(api,'mobile_port_guard',nullcontext)
    c,s,b,phone,path=business
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    app=FastAPI();app.state.api_secret='fixture-secret';app.include_router(api.router)
    app.add_middleware(BusinessResetActivityMiddleware)
    client=TestClient(app);client.headers.pop('X-Insurance-AI-Secret',None)
    headers={'X-Insurance-AI-Secret':'fixture-secret'}
    body={'confirmation':'복원','confirmed':True,'request_id':'b'*32,'backup_filename':receipt['backup_filename']}
    status=client.get('/business/reset/status',headers=headers).json()
    assert status.get('restore_candidate')==receipt['backup_filename']
    assert client.post('/business/reset/restore',json=body).status_code==401
    with REQUESTS.work():
        assert client.post('/business/reset/restore',headers=headers,json=body).status_code==409
    @contextmanager
    def busy():
        raise OSError('synthetic LAN listener');yield
    monkeypatch.setattr(api,'mobile_port_guard',busy)
    assert client.post('/business/reset/restore',headers=headers,json=body).status_code==409
    assert reset.sync_is_held(c)
    monkeypatch.setattr(api,'mobile_port_guard',nullcontext)
    assert client.post('/business/reset/restore',headers=headers,json={**body,'confirmed':False}).status_code==422
    response=client.post('/business/reset/restore',headers=headers,json=body)
    assert response.status_code==200,response.text
    assert response.json()['restored'] and not response.json()['sync_paused']
    assert client.get('/business/reset/status',headers=headers).json()['restore_candidate'] is None


def test_legacy_whole_db_restore_cannot_bypass_reset_hold(business,monkeypatch):
    import asyncio
    from fastapi import HTTPException
    import importlib
    router=importlib.import_module('database.router')
    c,s,b,phone,path=business
    service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    class Request:
        async def json(self):return {'filename':'synthetic.sqlite3'}
    def forbidden(*a,**k):raise AssertionError('Whole DB restore bypassed HOLD')
    monkeypatch.setattr(router._backup,'restore_backup',forbidden)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(router.post_backup_restore(Request(),conn=c))
    assert failure.value.status_code==409


@pytest.mark.parametrize('case',['wrong-key','tamper','schema','wrong-file','wrong-confirmation','foreign-db','failure','backup-failure'])
def test_restore_refusals_preserve_current_data_and_hold(business,monkeypatch,case):
    import base64,json
    from database import crypto
    c,s,b,phone,path=business
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    repo.create_customer(c,{'name':'KEEP-NEW-WORK'})
    kwargs: dict = dict(confirmation='복원',confirmed=True,request_id='b'*32,backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    if case=='wrong-key':
        monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(b'z'*32).decode());crypto.reset_cache()
    elif case=='tamper':(path/'backups'/receipt['backup_filename']).write_text('enc:v1:tampered')
    elif case=='schema':c.execute('ALTER TABLE customers ADD COLUMN unsupported TEXT');c.commit()
    elif case=='wrong-file':kwargs['backup_filename']='../outside.enc'
    elif case=='wrong-confirmation':kwargs['confirmation']=' 복원'
    elif case=='foreign-db':
        receipt['backup_sha256']='0'*64
        c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(receipt),reset.RESULT_KEY));c.commit()
    elif case=='failure':
        def fail(table):
            if table=='customers':raise RuntimeError('synthetic rollback')
        kwargs['fault_hook']=fail
    elif case=='backup-failure':
        def fail(*args):raise OSError('synthetic full disk')
        monkeypatch.setattr(reset,'_encrypted_safety_copy',fail)
    before=[tuple(r) for r in c.execute('SELECT * FROM customers')]
    devices=[tuple(r) for r in c.execute('SELECT * FROM sync_devices')]
    with pytest.raises(Exception):reset.restore_business_data(c,**kwargs)
    assert reset.sync_is_held(c)
    assert [tuple(r) for r in c.execute('SELECT * FROM customers')]==before
    assert [tuple(r) for r in c.execute('SELECT * FROM sync_devices')]==devices
    assert s.authenticate(phone.token).device_id==phone.device_id


def test_restore_preserves_new_work_in_decryptable_safety_copy_and_revocation(business):
    import base64,sqlite3
    from database import crypto
    c,s,b,phone,path=business
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    new=repo.create_customer(c,{'name':'NEW-WORK-SAFETY-COPY'})
    c.execute("UPDATE sync_devices SET revoked_at='synthetic-revoked' WHERE id=?",(phone.device_id,));c.commit()
    result=reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='b'*32,
        backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    raw=base64.b64decode(crypto.get_cipher().decrypt((path/'backups'/result['safety_copy']).read_text()))
    clone=sqlite3.connect(':memory:')
    try:
        clone.deserialize(raw[:18]+b'\x01\x01'+raw[20:])
        assert clone.execute('SELECT id FROM customers').fetchone()[0]==new['id']
        assert reset.sync_is_held(clone)
        assert clone.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    finally:clone.close()
    with pytest.raises(Exception):s.authenticate(phone.token)
    assert c.execute('SELECT revoked_at FROM sync_devices WHERE id=?',(phone.device_id,)).fetchone()[0]=='synthetic-revoked'


def test_repeated_resets_restore_lineage_without_premature_unhold(business):
    c,s,b,phone,path=business
    first=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    repo.create_customer(c,{'name':'INTERMEDIATE-PC-ONLY'})
    second=service()(c,confirmation='초기화',confirmed=True,request_id='b'*32,backup_directory=path/'backups')
    middle=reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='c'*32,
        backup_filename=second['backup_filename'],backup_directory=path/'backups')
    assert middle['sync_paused']
    final=reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='d'*32,
        backup_filename=first['backup_filename'],backup_directory=path/'backups')
    assert not final['sync_paused']
    assert len(s.pull(s.authenticate(phone.token),0).items)>0


def test_delayed_old_reset_request_cannot_erase_restored_workspace(business):
    c,s,b,phone,path=business
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='b'*32,
        backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    with pytest.raises(ValueError):
        service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    assert not reset.sync_is_held(c)
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==1


def test_existing_phone_queue_after_restore_uses_normal_conflict_preservation(business):
    from fastapi.testclient import TestClient
    from lan_sync.app import create_app
    c,s,b,phone,path=business
    customer_id=c.execute('SELECT id FROM customers').fetchone()[0]
    base=s.get_entity('customer',customer_id)
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    operation={'operation_id':'synthetic-offline-op','entity_type':'customer','entity_id':customer_id,
        'base':base,'patch':{'memo':'PHONE-OFFLINE-MEMO'},'deleted':False}
    headers={'Authorization':f'Bearer {phone.token}'}
    with TestClient(create_app(path/'synthetic.sqlite3'),base_url='https://test') as client:
        denied=client.post('/v1/sync',headers=headers,json={'cursor':0,'operations':[operation]})
        assert denied.status_code==409 and denied.json()['detail']=='business_reset_sync_hold'
    assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]==0
    reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='b'*32,
        backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    repo.update_customer(c,customer_id,{'memo':'PC-AFTER-RESTORE'})
    with TestClient(create_app(path/'synthetic.sqlite3'),base_url='https://test') as client:
        response=client.post('/v1/sync',headers=headers,json={'cursor':0,'operations':[operation]})
        assert response.status_code==200,response.text
        value=response.json()
        assert value['results'][0]['status']=='conflict'
        assert value['conflicts']
        assert 'PHONE-OFFLINE-MEMO' in str(value['conflicts'])
        assert not any(change.get('deleted') for change in value['changes'])
    assert repo.get_customer(c,customer_id)['memo']=='PC-AFTER-RESTORE'


def test_missing_key_restore_never_generates_or_persists_a_replacement_key(business,monkeypatch):
    from database import crypto
    c,s,b,phone,path=business
    receipt=service()(c,confirmation='초기화',confirmed=True,request_id='a'*32,backup_directory=path/'backups')
    monkeypatch.delenv('CUSTOMER_DB_KEY_B64');crypto.reset_cache()
    writes=[]
    monkeypatch.setattr(crypto,'_keyring_get',lambda:None)
    monkeypatch.setattr(crypto,'_keyfile_get',lambda:None)
    monkeypatch.setattr(crypto,'_keyring_set',lambda key:writes.append('keyring') or False)
    monkeypatch.setattr(crypto,'_keyfile_set',lambda key:writes.append('keyfile'))
    with pytest.raises(Exception):
        reset.restore_business_data(c,confirmation='복원',confirmed=True,request_id='b'*32,
            backup_filename=receipt['backup_filename'],backup_directory=path/'backups')
    assert writes==[], 'Recovery must not create a new key when the original is missing'
    assert reset.sync_is_held(c)
