import base64,json,os,sqlite3,subprocess,sys
from contextlib import closing
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
import account_migration as m
from account_workspace import VerifiedPrincipal
from protected_business_key import encode_key
from test_workspace_transition import fixture,old_workspace,A
B=VerifiedPrincipal(A.issuer,'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')

def review(tmp_path,mode):
 if mode=='empty-bootstrap':
  installation,source,w=fixture(tmp_path);files={'pdfs/document.pdf':'document.pdf'}
 else:
  installation,w=old_workspace(tmp_path);source=w.root;files={'config/customer-db.key':'config/customer-db.key','data/customers.sqlite3':'data/customers.sqlite3'}
 plan=m.plan_workspace_transition(source,installation,files,A,mode=mode)
 return installation,w,plan,m.OwnerApproval(A.issuer,A.subject,plan['binding']),Fernet.generate_key()

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
@pytest.mark.parametrize('phase',['backup','staging','archived','promotion','committed'])
def test_real_process_death_retry_and_rollback(tmp_path,mode,phase):
 installation,w,plan,approval,key=review(tmp_path,mode);before=m._tree_snapshot(w.root)
 payload=dict(plan=plan,issuer=A.issuer,subject=A.subject,key=key.decode(),phase=phase)
 child=subprocess.run([sys.executable,str(Path(__file__).with_name('workspace_transition_crash_fixture.py'))],input=json.dumps(payload),text=True,capture_output=True,timeout=45)
 assert child.returncode==73,child.stderr
 m.migrate(plan,A,approval,key)
 assert not m._transition_marker(w.root).exists()
 m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before

@pytest.mark.parametrize('kind',['record','rag','attachment','backup','mobile','wal','unknown','foreign-key','bad-key','hardlink'])
def test_nonempty_unknown_or_foreign_target_never_adopted(tmp_path,kind):
 installation,source,w=fixture(tmp_path)
 if kind=='record':
  with closing(sqlite3.connect(w.database)) as c:
   c.execute("INSERT INTO app_settings(key,value) VALUES('fixture','user setting')");c.commit()
 elif kind=='rag':
  with closing(sqlite3.connect(w.rag_database)) as c:
   c.execute('CREATE TABLE chunks(text TEXT)');c.execute("INSERT INTO chunks VALUES('SYNTHETIC RAG')");c.commit()
 elif kind=='foreign-key':w.key.write_bytes(encode_key(b'B'*32,B.issuer,B.subject))
 elif kind=='bad-key':w.key.write_bytes(b'broken')
 elif kind=='hardlink':os.link(w.key,w.key.with_name('linked.key'))
 else:
  relative={'attachment':'pdfs/a.pdf','backup':'data/backups/a.enc','mobile':'config/mobile-sync/device.json','wal':'data/customers.sqlite3-wal','unknown':'unknown.bin'}[kind]
  f=w.root/relative;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'SYNTHETIC')
 before={str(p.relative_to(w.root)):p.read_bytes() for p in w.root.rglob('*') if p.is_file()}
 with pytest.raises(Exception):m.plan_workspace_transition(source,installation,{'pdfs/document.pdf':'document.pdf'},A,mode='empty-bootstrap')
 assert before=={str(p.relative_to(w.root)):p.read_bytes() for p in w.root.rglob('*') if p.is_file()}

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
def test_review_snapshot_change_or_foreign_approval_never_writes(tmp_path,mode):
 installation,w,plan,approval,key=review(tmp_path,mode)
 with pytest.raises(m.MigrationError):m.migrate(plan,B,approval,key)
 (w.root/'pdfs').mkdir(exist_ok=True);(w.root/'pdfs/new.pdf').write_bytes(b'NEW DATA')
 before=m._tree_snapshot(w.root)
 with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,key)
 assert before==m._tree_snapshot(w.root)

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
def test_active_owner_lock_blocks_transition(tmp_path,mode):
 installation,w,plan,approval,key=review(tmp_path,mode);before=m._tree_snapshot(w.root)
 with m._lease(installation/'app.lock'):
  with pytest.raises(m.MigrationError,match='running'):m.migrate(plan,A,approval,key)
 assert before==m._tree_snapshot(w.root)

def test_wrong_legacy_key_and_omitted_files_rejected(tmp_path):
 installation,w=old_workspace(tmp_path);files={'config/customer-db.key':'config/customer-db.key','data/customers.sqlite3':'data/customers.sqlite3'}
 w.key.write_bytes(base64.b64encode(b'X'*32))
 with pytest.raises(Exception):m.plan_workspace_transition(w.root,installation,files,A,mode='same-owner-format')
 w.key.write_bytes(base64.b64encode(b'C'*32));(w.root/'pdfs').mkdir();(w.root/'pdfs/keep.pdf').write_bytes(b'KEEP')
 with pytest.raises(m.MigrationError,match='Complete'):m.plan_workspace_transition(w.root,installation,files,A,mode='same-owner-format')
