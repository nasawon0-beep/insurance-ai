"""Synthetic migrated-workspace library probe; no HTTP/listener/Auth substitution."""
import os,sys,json
from pathlib import Path
from types import SimpleNamespace
root=Path(sys.argv[1]);sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
os.environ.update({'CUSTOMER_DB_PATH':str(root/'data/customers.sqlite3'),'CUSTOMER_DB_KEYFILE':str(root/'config/customer-db.key'),'CUSTOMER_DB_KEYFILE_ONLY':'1','CUSTOMER_DB_KEY_B64':'','MOADOOM_ACCOUNT_ISSUER':'https://fixture.supabase.co/auth/v1','MOADOOM_ACCOUNT_SUBJECT':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','MOADOOM_ACCOUNT_EXPIRES_AT':'4102444800','RAG_DB_PATH':str(root/'data/rag-index.sqlite3'),'INSURANCE_AI_API_SECRET_FILE':str(root/'data/fixture.secret'),'COVERAGE_ANALYSIS_LOG_DIR':str(root/'logs')})
from database import db,repo,backup
from rag.store import VectorStore
from lan_sync.store import SyncStore
from lan_sync.bridge import ProductionBridge
c=db.connect()
try:
 assert repo.get_customer(c,'fixture-customer')['name']=='SYNTHETIC CUSTOMER'
 assert repo.update_customer(c,'fixture-customer',{'name':'SYNTHETIC SAVED'})['name']=='SYNTHETIC SAVED'
 assert repo.get_customer(c,'fixture-customer')['name']=='SYNTHETIC SAVED'
finally:c.close()
r=VectorStore()
try:
 assert r.search([1,0])[0]['text']=='SYNTHETIC OLD RAG'
 r.add([SimpleNamespace(chunk_id='new',doc_id='new',filename='fixture.pdf',page=1,text='SYNTHETIC NEW RAG')],[[1,0]],'fixture')
 assert any(x['text']=='SYNTHETIC NEW RAG' for x in r.search([1,0]))
finally:r.close()
s=SyncStore(os.environ['CUSTOMER_DB_PATH']);s.initialize()
try:
 bridge=ProductionBridge(s);bridge.initialize();bridge.refresh()
finally:s.close()
result=backup.make_backup(db_path=os.environ['CUSTOMER_DB_PATH'])
assert backup.list_backups(os.environ['CUSTOMER_DB_PATH'])
print(json.dumps({'customer_read_save':True,'rag_read_save':True,'backup_created':True,'mobile_bridge_refresh':True}))
