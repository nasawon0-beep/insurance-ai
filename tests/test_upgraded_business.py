import subprocess,sys,sqlite3
from pathlib import Path
from contextlib import closing
from cryptography.fernet import Fernet
import pytest
import account_migration as m
from test_workspace_transition import old_workspace,A

def test_migrated_workspace_real_customer_rag_backup_and_mobile_libraries(tmp_path):
 installation,w=old_workspace(tmp_path)
 with closing(sqlite3.connect(w.rag_database)) as c:
  c.execute('CREATE TABLE chunks(chunk_id TEXT,doc_id TEXT,filename TEXT,page INTEGER,text TEXT,embedding TEXT,embed_model TEXT)')
  c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',('old','old','fixture.pdf',1,'SYNTHETIC OLD RAG','[1,0]','fixture'));c.commit()
 files={name:name for name in ['config/customer-db.key','data/customers.sqlite3','data/rag-index.sqlite3']}
 plan=m.plan_workspace_transition(w.root,installation,files,A,mode='same-owner-format')
 approval=m.OwnerApproval(A.issuer,A.subject,plan['binding']);key=Fernet.generate_key()
 m.migrate(plan,A,approval,key)
 child=subprocess.run([sys.executable,str(Path(__file__).with_name('upgraded_business_fixture.py')),str(w.root)],capture_output=True,timeout=45)
 assert child.returncode==0,child.stdout+child.stderr
 before=m._tree_snapshot(w.root)
 with pytest.raises(m.MigrationError):m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before # New business writes must never be discarded by rollback.
