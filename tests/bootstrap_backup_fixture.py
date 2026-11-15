import os,sys,json
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
os.environ.update({'CUSTOMER_DB_PATH':str(root/'data/customers.sqlite3'),'CUSTOMER_DB_KEYFILE':str(root/'config/customer-db.key'),'CUSTOMER_DB_KEYFILE_ONLY':'1','CUSTOMER_DB_KEY_B64':'','MOADOOM_ACCOUNT_ISSUER':'https://fixture.supabase.co/auth/v1','MOADOOM_ACCOUNT_SUBJECT':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'})
from database.backup import startup_backup
assert startup_backup()['created']
print(json.dumps({'synthetic_startup_backup_created':True}))
