"""Offline migration mechanics only; no real central-auth/owner approval claim."""
import importlib.util
import base64
import sqlite3
from contextlib import closing
import pytest
from cryptography.fernet import Fernet
from account_workspace import VerifiedPrincipal, workspace_for

A=VerifiedPrincipal('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
B=VerifiedPrincipal(A.issuer,'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')

def module():
    assert importlib.util.find_spec('account_migration') is not None, 'Migration tool is not implemented'
    import account_migration
    return account_migration

@pytest.mark.parametrize('installation',['Install One 한글','Install Two spaces'])
@pytest.mark.parametrize('failure',['backup','staging','promotion','committed'])
def test_failure_retry_rollback_preserves_source_and_other_account(tmp_path,installation,failure):
    m=module()
    root=tmp_path/installation; root.mkdir()
    legacy=root/'legacy'; legacy.mkdir()
    database=legacy/'customers.sqlite3'
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("CREATE TABLE customers(name TEXT); INSERT INTO customers VALUES('PRIVATE FIXTURE'); CREATE TABLE sync_devices(token_digest TEXT); INSERT INTO sync_devices VALUES('old-phone-token');")
    (legacy/'customer-db.key').write_bytes(base64.b64encode(b'Q'*32))
    original=database.read_bytes()
    other=workspace_for(root,B).root; other.mkdir(parents=True)
    (other/'sentinel').write_bytes(b'B UNCHANGED')
    mapping={'data/customers.sqlite3':'customers.sqlite3','config/customer-db.key':'customer-db.key'}
    plan=m.plan_migration(legacy,root,mapping)
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding'])
    key=Fernet.generate_key()
    def fail(phase):
        if phase==failure: raise RuntimeError('simulated power loss')
    with pytest.raises(RuntimeError,match='simulated power loss'):
        m.migrate(plan,A,approval,key,checkpoint=fail)
    result=m.migrate(plan,A,approval,key)
    assert result['phase']=='committed'
    target=workspace_for(root,A).root
    with closing(sqlite3.connect(target/'data/customers.sqlite3')) as conn:
        assert conn.execute('SELECT name FROM customers').fetchall()==[('PRIVATE FIXTURE',)]
        assert conn.execute('SELECT COUNT(*) FROM sync_devices').fetchone()[0]==0
    from protected_business_key import read_protected
    assert read_protected(target/'config/customer-db.key',A.issuer,A.subject)==b'Q'*32
    assert (legacy/'customer-db.key').read_bytes()==base64.b64encode(b'Q'*32)
    assert database.read_bytes()==original
    assert (other/'sentinel').read_bytes()==b'B UNCHANGED'
    backup=result['backup']
    assert b'PRIVATE FIXTURE' not in open(backup,'rb').read()
    assert m.migrate(plan,A,approval,key)['phase']=='committed'
    rolled=m.rollback(plan,A,approval,key)
    assert rolled['phase']=='rolled_back' and not target.exists()
    assert database.read_bytes()==original
    assert (other/'sentinel').read_bytes()==b'B UNCHANGED'
    assert m.rollback(plan,A,approval,key)['phase']=='rolled_back'
    with pytest.raises(m.MigrationError):m.migrate(plan,B,approval,key)
    with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,Fernet.generate_key())

def test_encrypted_backup_recovers_missing_original_without_active_publication(tmp_path):
    m=module(); legacy=tmp_path/'legacy';legacy.mkdir()
    original=b'PRIVATE ORIGINAL ONLY'
    (legacy/'data.bin').write_bytes(original)
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data.bin':'data.bin'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    m.migrate(plan,A,approval,key)
    (legacy/'data.bin').unlink()  # synthetic loss injection only
    assert callable(getattr(m,'restore_backup',None)), 'Verified backup recovery is missing'
    restored=m.restore_backup(plan,A,approval,key)
    assert (restored/'data.bin').read_bytes()==original
    assert not (legacy/'data.bin').exists()
    assert m.restore_backup(plan,A,approval,key)==restored
    with pytest.raises(m.MigrationError):m.restore_backup(plan,B,approval,key)


@pytest.mark.parametrize('destinations',[
    ['pdfs/A.bin','pdfs/a.bin'],['pdfs/data.bin.','pdfs/data2.bin'],
    ['pdfs/NUL.bin','pdfs/data2.bin'],['pdfs/../data.bin','pdfs/data2.bin'],
])
def test_windows_path_aliases_are_rejected(tmp_path,destinations):
    m=module();legacy=tmp_path/'legacy';legacy.mkdir()
    (legacy/'a').write_bytes(b'A');(legacy/'b').write_bytes(b'B')
    with pytest.raises(m.MigrationError):
        m.plan_migration(legacy,tmp_path,dict(zip(destinations,['a','b'])))


def test_running_app_and_existing_destination_are_not_overwritten(tmp_path):
    m=module();legacy=tmp_path/'legacy';legacy.mkdir();(legacy/'data').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data':'data'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    with m._lease(tmp_path/'app.lock'):
        with pytest.raises(m.MigrationError,match='running'):m.migrate(plan,A,approval,key)
    target=workspace_for(tmp_path,A).root;target.mkdir(parents=True);(target/'sentinel').write_bytes(b'keep')
    with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,key)
    assert (target/'sentinel').read_bytes()==b'keep'


@pytest.mark.parametrize('failure',['backup','staging','promotion','committed'])
def test_abrupt_process_exit_releases_locks_and_recovers(tmp_path,failure):
    import json,subprocess,sys
    from pathlib import Path
    m=module();legacy=tmp_path/'legacy';legacy.mkdir();(legacy/'data').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data':'data'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    keyfile=tmp_path/'fixture-key';keyfile.write_bytes(key)
    config=tmp_path/'fixture.json';config.write_text(json.dumps({'plan':plan,'principal':{'issuer':A.issuer,'subject':A.subject},'failure':failure,'keyfile':str(keyfile)}))
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('account_migration_crash_fixture.py')),str(config)],capture_output=True,timeout=15)
    assert result.returncode==73,result.stderr
    assert m.migrate(plan,A,approval,key)['phase']=='committed'
    assert (workspace_for(tmp_path,A).root/'pdfs/data').read_bytes()==b'original'


def test_staging_directory_junction_is_rejected_even_when_empty(tmp_path):
    import os,subprocess
    if os.name!='nt':pytest.skip('Windows junction test')
    m=module();legacy=tmp_path/'legacy';legacy.mkdir();(legacy/'data').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data':'data'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    def stop(phase):
        if phase=='staging':raise RuntimeError('stop')
    with pytest.raises(RuntimeError):m.migrate(plan,A,approval,key,checkpoint=stop)
    other=tmp_path/'foreign-empty';other.mkdir()
    stage=tmp_path/'.account-migrations'/plan['binding']/'staging'
    created=subprocess.run(['cmd.exe','/c','mklink','/J',str(stage/'config'),str(other)],capture_output=True)
    assert created.returncode==0,created.stderr
    with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,key)
    assert not workspace_for(tmp_path,A).root.exists()


def test_interrupted_staging_file_write_can_retry(tmp_path,monkeypatch):
    import os
    m=module();legacy=tmp_path/'legacy';legacy.mkdir();(legacy/'data').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data':'data'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    replace=os.replace
    def fail(source,destination):
        if 'staging' in destination.parts:raise OSError('injected write interruption')
        return replace(source,destination)
    with monkeypatch.context() as patcher:
        patcher.setattr(m.os,'replace',fail)
        with pytest.raises(OSError,match='injected'):m.migrate(plan,A,approval,key)
    assert m.migrate(plan,A,approval,key)['phase']=='committed'


def test_corrupt_backup_cannot_restore_or_change_active_data(tmp_path):
    from pathlib import Path
    m=module();legacy=tmp_path/'legacy';legacy.mkdir();(legacy/'data').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data':'data'})
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
    state=m.migrate(plan,A,approval,key)
    Path(state['backup']).write_bytes(b'tampered synthetic ciphertext')
    with pytest.raises(m.MigrationError):m.restore_backup(plan,A,approval,key)
    with pytest.raises(m.MigrationError):m.rollback(plan,A,approval,key)
    assert (workspace_for(tmp_path,A).root/'pdfs/data').read_bytes()==b'original'


def test_real_customer_cipher_and_key_survive_migration(tmp_path,monkeypatch):
    from database import db,repo,crypto
    m=module();legacy=tmp_path/'legacy';legacy.mkdir()
    monkeypatch.setenv('CUSTOMER_DB_PATH',str(legacy/'customers.sqlite3'))
    monkeypatch.setenv('CUSTOMER_DB_KEYFILE',str(legacy/'customer-db.key'))
    monkeypatch.setenv('CUSTOMER_DB_KEYFILE_ONLY','1')
    monkeypatch.setenv('CUSTOMER_DB_KEY_B64','')
    monkeypatch.setattr(crypto,'_KEYFILE_FALLBACK',legacy/'customer-db.key')
    crypto.reset_cache()
    try:
        with closing(db.connect()) as conn:
            db.init_schema(conn)
            customer=repo.create_customer(conn,{'name':'ENCRYPTED-OWNER-ONLY','phone':'01000000000'})
        original_key=(legacy/'customer-db.key').read_bytes()
        assert b'ENCRYPTED-OWNER-ONLY' not in (legacy/'customers.sqlite3').read_bytes()
        plan=m.plan_migration(legacy,tmp_path,{'data/customers.sqlite3':'customers.sqlite3','config/customer-db.key':'customer-db.key'})
        approval=m.OwnerApproval(A.issuer,A.subject,plan['binding'])
        m.migrate(plan,A,approval,Fernet.generate_key())
        target=workspace_for(tmp_path,A)
        from protected_business_key import read_protected
        assert read_protected(target.key,A.issuer,A.subject)==base64.b64decode(original_key)
        assert (legacy/'customer-db.key').read_bytes()==original_key
        monkeypatch.setenv('MOADOOM_ACCOUNT_ISSUER',A.issuer)
        monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',A.subject)
        monkeypatch.setenv('CUSTOMER_DB_PATH',str(target.database))
        monkeypatch.setenv('CUSTOMER_DB_KEYFILE',str(target.key))
        monkeypatch.setattr(crypto,'_KEYFILE_FALLBACK',target.key)
        crypto.reset_cache()
        with closing(db.connect()) as conn:
            assert repo.get_customer(conn,customer['id'])['name']=='ENCRYPTED-OWNER-ONLY'
    finally:crypto.reset_cache()


def test_unconfirmed_owner_and_source_change_fail_closed(tmp_path):
    m=module(); legacy=tmp_path/'legacy';legacy.mkdir()
    (legacy/'data.bin').write_bytes(b'original')
    plan=m.plan_migration(legacy,tmp_path,{'pdfs/data.bin':'data.bin'})
    key=Fernet.generate_key()
    with pytest.raises(m.MigrationError):m.migrate(plan,A,None,key)
    approval=m.OwnerApproval(A.issuer,A.subject,plan['binding'])
    (legacy/'data.bin').write_bytes(b'changed')
    with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,key)
    assert not workspace_for(tmp_path,A).root.exists()
