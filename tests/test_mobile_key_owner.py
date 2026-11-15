import os,sys,json,subprocess
from pathlib import Path

def test_fresh_mobile_bridge_needs_verified_launcher_dpapi_owner(tmp_path):
 env=os.environ.copy();env.update({'CUSTOMER_DB_PATH':str(tmp_path/'customers.sqlite3'),'CUSTOMER_DB_KEYFILE':str(tmp_path/'customer.key'),'CUSTOMER_DB_KEYFILE_ONLY':'1','CUSTOMER_DB_KEY_B64':'','MOADOOM_ACCOUNT_ISSUER':'https://fixture.supabase.co/auth/v1','MOADOOM_ACCOUNT_SUBJECT':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','INSURANCE_AI_API_SECRET_FILE':str(tmp_path/'api.secret'),'RAG_DB_PATH':str(tmp_path/'rag.sqlite3'),'COVERAGE_ANALYSIS_LOG_DIR':str(tmp_path/'logs')})
 fixture=Path(__file__).with_name('mobile_key_owner_fixture.py');identifier=tmp_path/'synthetic-customer-id'
 def run(mode,e):return subprocess.run([sys.executable,str(fixture),mode,str(identifier)],env=e,capture_output=True,timeout=30)
 seed=run('seed',env);assert seed.returncode==0,seed.stdout+seed.stderr
 key=(tmp_path/'customer.key').read_bytes()
 missing=env.copy();missing.pop('MOADOOM_ACCOUNT_ISSUER');missing.pop('MOADOOM_ACCOUNT_SUBJECT')
 assert run('refresh',missing).returncode==9
 foreign=env.copy();foreign['MOADOOM_ACCOUNT_SUBJECT']='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
 assert run('refresh',foreign).returncode==9
 assert run('refresh',env).returncode==0
 assert (tmp_path/'customer.key').read_bytes()==key
