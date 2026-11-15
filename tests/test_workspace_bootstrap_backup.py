from contextlib import closing
from pathlib import Path
import subprocess,sys,sqlite3
import pytest
from cryptography.fernet import Fernet
import account_migration as m
from test_workspace_transition import fixture,old_workspace,A
from protected_business_key import encode_key
import base64,json
from database.crypto import FieldCipher

def boot_backup(w):
 p=subprocess.run([sys.executable,str(Path(__file__).with_name('bootstrap_backup_fixture.py')),str(w.root)],capture_output=True,timeout=30)
 assert p.returncode==0,p.stdout+p.stderr

def test_actual_bootstrap_backup_is_verified_empty_and_preserved(tmp_path):
 installation,source,w=fixture(tmp_path);boot_backup(w)
 before=m._tree_snapshot(w.root)
 plan=m.plan_workspace_transition(source,installation,{'pdfs/document.pdf':'document.pdf'},A,mode='empty-bootstrap')
 approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
 m.migrate(plan,A,approval,key)
 archive=installation/'.account-migrations'/plan['binding']/'previous-workspace'
 assert m._tree_snapshot(archive)==before
 m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before

def test_same_owner_format_keeps_authenticated_backup_bytes(tmp_path):
 installation,w=old_workspace(tmp_path)
 w.key.write_bytes(encode_key(b'C'*32,A.issuer,A.subject));boot_backup(w)
 w.key.write_bytes(base64.b64encode(b'C'*32))
 snapshot=m._tree_snapshot(w.root)
 files={name:name for name in snapshot['files'] if name!='data/local-engine-api.secret'}
 plan=m.plan_workspace_transition(w.root,installation,files,A,mode='same-owner-format')
 approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
 m.migrate(plan,A,approval,key)
 for name,digest in snapshot['files'].items():
  if name.startswith('data/backups/'):assert m._sha((w.root/name).read_bytes())==digest
 m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==snapshot

@pytest.mark.parametrize('fault',['owner','digest','unbound','plaintext'])
def test_bootstrap_backup_authority_tampering_is_rejected(tmp_path,fault):
 installation,source,w=fixture(tmp_path);boot_backup(w)
 marker=next((w.root/'data/backups').glob('*.owner.enc'))
 if fault=='unbound':marker.unlink()
 elif fault=='plaintext':marker.write_text('{}')
 else:
  cipher=FieldCipher(b'B'*32,'fixture');manifest=json.loads(cipher.decrypt(marker.read_text()))
  if fault=='owner':manifest['owner'][1]='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
  else:manifest['sha256']='0'*64
  marker.write_text(cipher.encrypt(json.dumps(manifest)))
 before=m._tree_snapshot(w.root)
 with pytest.raises(m.MigrationError):m.plan_workspace_transition(source,installation,{'pdfs/document.pdf':'document.pdf'},A,mode='empty-bootstrap')
 assert m._tree_snapshot(w.root)==before

def test_empty_current_db_with_nonempty_backup_is_not_empty(tmp_path):
 installation,source,w=fixture(tmp_path)
 with closing(sqlite3.connect(w.database)) as c:
  c.execute("INSERT INTO app_settings(key,value) VALUES('user','SYNTHETIC DATA')");c.commit()
 boot_backup(w)
 with closing(sqlite3.connect(w.database)) as c:c.execute('DELETE FROM app_settings');c.commit()
 before=m._tree_snapshot(w.root)
 with pytest.raises(m.MigrationError):m.plan_workspace_transition(source,installation,{'pdfs/document.pdf':'document.pdf'},A,mode='empty-bootstrap')
 assert m._tree_snapshot(w.root)==before
