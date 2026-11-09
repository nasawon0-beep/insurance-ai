import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
from lan_sync.store import SyncStore, AuthError, ValidationError

def test_hide_revoked_keeps_security_and_all_data(tmp_path):
    s=SyncStore(tmp_path/'synthetic.sqlite3');s.initialize()
    try:
        code=s.create_pairing_code('test');d=s.pair_device(code.code,'synthetic','192.0.2.1')
        s.seed_synthetic_customer('c1',{'name':'Synthetic'})
        auth=s.authenticate(d.token)
        s.push(auth,[{'operation_id':'synthetic-delete','entity_type':'customer','entity_id':'c1','base':{'name':'Synthetic'},'patch':{},'deleted':True}])
        s.seed_synthetic_customer('keep',{'name':'Unchanged customer'})
        s.conn.execute('INSERT INTO sync_tombstone_acks(device_id,cursor) SELECT ?,cursor FROM sync_changes', (d.device_id,))
        assert s.conn.execute('SELECT COUNT(*) FROM sync_operations').fetchone()[0] > 0
        assert s.conn.execute('SELECT COUNT(*) FROM sync_tombstone_acks').fetchone()[0] > 0
        s.revoke_device(d.device_id)
        tables=['sync_devices','sync_entities','sync_changes','sync_operations','sync_conflicts','sync_tombstone_acks','sync_recovery_codes']
        before={t:[tuple(r) for r in s.conn.execute('SELECT * FROM '+t)] for t in tables}
        assert callable(getattr(s,'hide_revoked_device',None)), 'non-destructive list removal missing'
        s.hide_revoked_device(d.device_id);s.hide_revoked_device(d.device_id)
        assert s.list_devices()==[]
        assert before=={t:[tuple(r) for r in s.conn.execute('SELECT * FROM '+t)] for t in tables}
        with pytest.raises(AuthError):s.authenticate(d.token)
        instance='0'*32
        recovery=s.create_recovery_code(d.device_id,instance)
        recovered=s.recover_device(recovery.code,'replacement-token-long-enough-0000001',d.device_id,instance,instance,'192.0.2.1')
        assert s.list_devices()[0]['id']==d.device_id
        assert s.authenticate(recovered.token).device_id==d.device_id
        with pytest.raises(AuthError):s.authenticate(d.token)
        with pytest.raises(ValidationError):s.hide_revoked_device(d.device_id)
        with pytest.raises(ValidationError):s.hide_revoked_device('missing')
        s.revoke_device(d.device_id)
        assert s.list_devices()[0]['id']==d.device_id, 'new revocation after recovery must be visible'
    finally:s.close()

def test_local_admin_confirmation_and_restart_persistence(tmp_path, monkeypatch, capsys):
    from lan_sync import admin
    db=tmp_path/'synthetic-admin.sqlite3'
    s=SyncStore(db);s.initialize()
    try:
        c=s.create_pairing_code('test');d=s.pair_device(c.code,'test','192.0.2.1');s.revoke_device(d.device_id)
        args=['admin','--db',str(db),'hide-revoked-device',d.device_id]
        monkeypatch.setattr(sys,'argv',args)
        with pytest.raises(SystemExit):admin.main()
        assert len(s.list_devices())==1
        monkeypatch.setattr(sys,'argv',args+['--confirmed'])
        admin.main();admin.main()
        assert s.list_devices()==[]
        with pytest.raises(AuthError):s.authenticate(d.token)
    finally:s.close()
    reopened=SyncStore(db);reopened.initialize()
    try:
        assert reopened.list_devices()==[]
        assert reopened.conn.execute('SELECT revoked_at FROM sync_devices WHERE id=?',(d.device_id,)).fetchone()[0] is not None
    finally:reopened.close()
    capsys.readouterr()
