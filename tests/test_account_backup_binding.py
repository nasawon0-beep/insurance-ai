import base64,hashlib,shutil,sqlite3
import pytest
from database import backup,crypto
A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
def bind(monkeypatch,root,subject):
 root.mkdir(exist_ok=True)
 monkeypatch.setenv('CUSTOMER_DB_PATH',str(root/'customers.sqlite3'))
 monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(subject[0].encode()*32).decode())
 monkeypatch.setenv('MOADOOM_ACCOUNT_ISSUER','https://fixture.supabase.co/auth/v1')
 monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',subject)
 crypto.reset_cache()
 return root/'customers.sqlite3'
def seed(path,label):
 c=sqlite3.connect(path)
 try:c.execute('create table payload(value text)');c.execute('insert into payload values(?)',(label,));c.commit()
 finally:c.close()
def test_foreign_copied_backup_cannot_replace_own_database(tmp_path,monkeypatch):
 b=bind(monkeypatch,tmp_path/'B',B);seed(b,'foreign fixture')
 receipt=backup.make_backup();source=backup.backup_dir()/receipt['filename']
 a=bind(monkeypatch,tmp_path/'A',A);seed(a,'own fixture')
 before=a.read_bytes();dest=backup.backup_dir()/source.name;shutil.copy2(source,dest)
 manifest=source.with_suffix(source.suffix+'.owner.enc')
 if manifest.exists():shutil.copy2(manifest,dest.with_suffix(dest.suffix+'.owner.enc'))
 with pytest.raises(ValueError):backup.restore_backup(dest.name)
 assert a.read_bytes()==before
 crypto.reset_cache()

@pytest.mark.parametrize('damage',['none','missing','tampered','issuer','subject'])
def test_own_backup_roundtrip_and_fail_closed_integrity(tmp_path,monkeypatch,damage):
 path=bind(monkeypatch,tmp_path/'own',A);seed(path,'before')
 result=backup.make_backup();snap=backup.backup_dir()/result['filename']
 c=sqlite3.connect(path)
 try:c.execute("update payload set value='after'");c.commit()
 finally:c.close()
 before=path.read_bytes()
 if damage=='missing':backup._owner_file(snap).unlink()
 elif damage=='tampered':
  with snap.open('ab') as f:f.write(b'changed')
 elif damage=='issuer':monkeypatch.setenv('MOADOOM_ACCOUNT_ISSUER','https://other.supabase.co/auth/v1')
 elif damage=='subject':monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',B)
 if damage=='none':
  restored=backup.restore_backup(snap.name)
  c=sqlite3.connect(path)
  try:assert c.execute('select value from payload').fetchone()[0]=='before'
  finally:c.close()
  assert backup._owner_file(backup.backup_dir()/restored['safety_copy']).is_file()
 else:
  with pytest.raises(ValueError):backup.restore_backup(snap.name)
  assert path.read_bytes()==before
 crypto.reset_cache()
