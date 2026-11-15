"""Synthetic existing-workspace boundary; no publication/production upgrade tool."""
import base64,sqlite3,os
from contextlib import closing
import pytest
from cryptography.fernet import Fernet
from account_workspace import VerifiedPrincipal,workspace_for
from account_migration import plan_migration,migrate,OwnerApproval,MigrationError
from protected_business_key import convert_legacy,read_protected
from database.crypto import FieldCipher
from rag.store import VectorStore,convert_legacy_store
A=VerifiedPrincipal('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')

def existing(root):
 w=workspace_for(root,A);w.key.parent.mkdir(parents=True);w.database.parent.mkdir(parents=True)
 w.key.write_bytes(base64.b64encode(b'U'*32))
 with closing(sqlite3.connect(w.database)) as db:
  db.execute('CREATE TABLE customers(name TEXT)');db.execute('INSERT INTO customers VALUES(?)',(FieldCipher(b'U'*32,'fixture').encrypt('PRIVATE CRM'),));db.commit()
 with closing(sqlite3.connect(w.rag_database)) as db:
  db.execute('CREATE TABLE chunks(chunk_id TEXT,doc_id TEXT,filename TEXT,page INTEGER,text TEXT,embedding TEXT,embed_model TEXT)')
  db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',('c','d','PRIVATE.pdf',1,'PRIVATE RAG','[1,0]','fixture'));db.commit()
 return w

def test_existing_encrypted_workspace_is_not_adopted_or_overwritten(tmp_path):
 w=existing(tmp_path);before={p:p.read_bytes() for p in [w.key,w.database,w.rag_database]}
 plan=plan_migration(w.root,tmp_path,{'config/customer-db.key':'config/customer-db.key','data/customers.sqlite3':'data/customers.sqlite3','data/rag-index.sqlite3':'data/rag-index.sqlite3'})
 with pytest.raises(MigrationError,match='Existing workspace'):migrate(plan,A,OwnerApproval(A.issuer,A.subject,plan['binding']),Fernet.generate_key())
 with pytest.raises(ValueError):read_protected(w.key,A.issuer,A.subject)
 with pytest.raises(ValueError,match='Legacy plaintext'):VectorStore(w.rag_database,cipher=FieldCipher(b'U'*32,'fixture'),owner=[A.issuer,A.subject])
 assert all(p.read_bytes()==data for p,data in before.items())

def test_normal_login_precreated_empty_target_blocks_legacy_import(tmp_path):
 source=existing(tmp_path/'legacy-fixture');target=workspace_for(tmp_path/'profile',A)
 target.root.mkdir(parents=True) # Normal native login creates this before engine bootstrap.
 before={p:p.read_bytes() for p in [source.key,source.database,source.rag_database]}
 plan=plan_migration(source.root,tmp_path/'profile',{'config/customer-db.key':'config/customer-db.key','data/customers.sqlite3':'data/customers.sqlite3','data/rag-index.sqlite3':'data/rag-index.sqlite3'})
 with pytest.raises(MigrationError,match='Existing workspace'):
  migrate(plan,A,OwnerApproval(A.issuer,A.subject,plan['binding']),Fernet.generate_key())
 assert list(target.root.iterdir())==[]
 assert all(p.read_bytes()==data for p,data in before.items())

def test_separate_conversion_failure_retry_preserves_active_workspace(tmp_path,monkeypatch):
 w=existing(tmp_path);before={p:p.read_bytes() for p in [w.key,w.database,w.rag_database]}
 candidate=tmp_path/'unpublished-candidate';candidate.mkdir();keyfile=candidate/'protected.key';rag=candidate/'protected-rag.sqlite3'
 convert_legacy(w.key,keyfile,A.issuer,A.subject,confirmed=True)
 key=read_protected(keyfile,A.issuer,A.subject);cipher=FieldCipher(key,'synthetic-review')
 real_link=os.link
 def interrupted(src,dst):
  if str(dst)==str(rag):raise OSError('synthetic publication interruption')
  return real_link(src,dst)
 with monkeypatch.context() as m:
  m.setattr(os,'link',interrupted)
  with pytest.raises(OSError,match='interruption'):convert_legacy_store(w.rag_database,rag,cipher=cipher,owner=[A.issuer,A.subject],confirmed=True)
 assert not rag.exists() and not list(candidate.glob('*.pending-*'))
 convert_legacy_store(w.rag_database,rag,cipher=cipher,owner=[A.issuer,A.subject],confirmed=True)
 s=VectorStore(rag,cipher=cipher,owner=[A.issuer,A.subject])
 try:assert s.search([1,0])[0]['text']=='PRIVATE RAG'
 finally:s.close()
 with pytest.raises(FileExistsError):convert_legacy_store(w.rag_database,rag,cipher=cipher,owner=[A.issuer,A.subject],confirmed=True)
 assert all(p.read_bytes()==data for p,data in before.items())
 assert b'PRIVATE RAG' not in rag.read_bytes()
