"""Fresh synthetic LAN bridge process; no network listener or operating profile."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
from database import db,repo
from lan_sync.store import SyncStore
from lan_sync.bridge import ProductionBridge
try:
 if sys.argv[1]=='seed':
  c=db.connect();db.init_schema(c);created=repo.create_customer(c,{'name':'SYNTHETIC DPAPI MOBILE'});c.close()
  Path(sys.argv[2]).write_text(created['id'])
 s=SyncStore(__import__('os').environ['CUSTOMER_DB_PATH']);s.initialize()
 try:
  bridge=ProductionBridge(s);bridge.initialize();bridge.refresh()
  assert repo.get_customer(s.conn,Path(sys.argv[2]).read_text())['name']=='SYNTHETIC DPAPI MOBILE'
 finally:s.close()
 print(json.dumps({'ok':True}))
except Exception as error:
 print(json.dumps({'ok':False,'error_type':type(error).__name__}));sys.exit(9)
