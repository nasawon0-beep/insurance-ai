from pathlib import Path
from contextlib import closing
import sqlite3
import base64
import pytest
from database.crypto import FieldCipher
from cryptography.fernet import Fernet
import account_migration as m
from account_workspace import VerifiedPrincipal,workspace_for
from protected_business_key import encode_key
from database.db import init_schema
A=VerifiedPrincipal('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')

def fixture(tmp_path):
 installation=tmp_path/'installation';installation.mkdir()
 source=tmp_path/'legacy';source.mkdir();(source/'document.pdf').write_bytes(b'SYNTHETIC LEGACY PDF')
 w=workspace_for(installation,A);w.key.parent.mkdir(parents=True);w.database.parent.mkdir(parents=True)
 w.key.write_bytes(encode_key(b'B'*32,A.issuer,A.subject))
 with closing(sqlite3.connect(w.database)) as c:
  c.row_factory=sqlite3.Row;init_schema(c)
 (w.root/'data/local-engine-api.secret').write_text('a'*64)
 return installation,source,w

def test_reviewed_empty_bootstrap_is_preserved_not_deleted(tmp_path):
 installation,source,w=fixture(tmp_path)
 before={str(p.relative_to(w.root)):p.read_bytes() for p in w.root.rglob('*') if p.is_file()}
 plan=m.plan_workspace_transition(source,installation,{'pdfs/document.pdf':'document.pdf'},A,mode='empty-bootstrap')
 approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
 result=m.migrate(plan,A,approval,key)
 assert result['phase']=='committed'
 assert (w.root/'pdfs/document.pdf').read_bytes()==b'SYNTHETIC LEGACY PDF'
 archive=installation/'.account-migrations'/plan['binding']/'previous-workspace'
 assert {str(p.relative_to(archive)):p.read_bytes() for p in archive.rglob('*') if p.is_file()}==before
 m.rollback(plan,A,approval,key)
 assert {str(p.relative_to(w.root)):p.read_bytes() for p in w.root.rglob('*') if p.is_file()}==before
 assert (source/'document.pdf').read_bytes()==b'SYNTHETIC LEGACY PDF'

def old_workspace(tmp_path):
 installation,_,w=fixture(tmp_path)
 w.key.write_bytes(base64.b64encode(b'C'*32))
 with closing(sqlite3.connect(w.database)) as c:
     c.execute("INSERT INTO customers(id,name,created_at,updated_at) VALUES(?,?,'2026-01-01','2026-01-01')",('fixture-customer',FieldCipher(b'C'*32,'fixture').encrypt('SYNTHETIC CUSTOMER')));c.commit()
 return installation,w

def test_same_owner_format_upgrade_preserves_key_data_and_rollback(tmp_path):
 installation,w=old_workspace(tmp_path)
 before=m._tree_snapshot(w.root)
 files={'config/customer-db.key':'config/customer-db.key','data/customers.sqlite3':'data/customers.sqlite3'}
 plan=m.plan_workspace_transition(w.root,installation,files,A,mode='same-owner-format')
 approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
 m.migrate(plan,A,approval,key)
 from protected_business_key import read_protected
 assert read_protected(w.key,A.issuer,A.subject)==b'C'*32
 with closing(sqlite3.connect(w.database)) as c:
     assert FieldCipher(b'C'*32,'fixture').decrypt(c.execute('SELECT name FROM customers').fetchone()[0])=='SYNTHETIC CUSTOMER'
 m.migrate(plan,A,approval,key) # Resume verifies archived original, not converted active bytes.
 m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before

