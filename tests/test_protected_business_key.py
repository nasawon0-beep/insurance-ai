import base64,json,os
from pathlib import Path
import pytest
import protected_business_key as keys
A=('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
B=(A[0],'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
def test_bound_cipher_writes_dpapi_and_refuses_legacy(tmp_path,monkeypatch):
 from database import crypto
 monkeypatch.setenv('MOADOOM_ACCOUNT_ISSUER',A[0]);monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',A[1])
 monkeypatch.setenv('CUSTOMER_DB_KEYFILE_ONLY','1');monkeypatch.delenv('CUSTOMER_DB_KEY_B64',raising=False)
 monkeypatch.setenv('CUSTOMER_DB_PATH',str(tmp_path/'empty.sqlite3'))
 path=tmp_path/'business.key';monkeypatch.setattr(crypto,'_KEYFILE_FALLBACK',path);crypto.reset_cache()
 try:
  cipher=crypto.get_cipher();encrypted=cipher.encrypt('synthetic private text')
  assert json.loads(path.read_text())['format']==keys.FORMAT
  crypto.reset_cache();assert crypto.get_cipher().decrypt(encrypted)=='synthetic private text'
  monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',B[1])
  with pytest.raises(ValueError):crypto.get_cipher()
  monkeypatch.setenv('MOADOOM_ACCOUNT_SUBJECT',A[1]);crypto.reset_cache()
  path.write_bytes(base64.b64encode(b'L'*32));before=path.read_bytes()
  with pytest.raises(ValueError):crypto.get_cipher()
  assert path.read_bytes()==before
 finally:crypto.reset_cache()

@pytest.mark.skipif(os.name!='nt',reason='real Windows DPAPI required')
def test_real_dpapi_roundtrip_owner_tamper_and_legacy_conversion(tmp_path):
 raw=b'K'*32;target=tmp_path/'customer-db.key'
 keys.write_protected(target,raw,*A)
 assert base64.b64encode(raw) not in target.read_bytes()
 assert keys.read_protected(target,*A)==raw
 with pytest.raises(ValueError):keys.read_protected(target,*B)
 document=json.loads(target.read_text());document['owner']=list(B)
 target.write_text(json.dumps(document))
 with pytest.raises(ValueError):keys.read_protected(target,*B)
 legacy=tmp_path/'legacy.key';legacy.write_bytes(base64.b64encode(raw));saved=legacy.read_bytes()
 with pytest.raises(ValueError):keys.read_protected(legacy,*A)
 with pytest.raises(ValueError):keys.convert_legacy(legacy,tmp_path/'converted.key',*A,confirmed=False)
 keys.convert_legacy(legacy,tmp_path/'converted.key',*A,confirmed=True)
 assert legacy.read_bytes()==saved and keys.read_protected(tmp_path/'converted.key',*A)==raw
 with pytest.raises(FileExistsError):keys.convert_legacy(legacy,tmp_path/'converted.key',*A,confirmed=True)
