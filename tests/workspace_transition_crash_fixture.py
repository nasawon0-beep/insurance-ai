import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
from account_workspace import VerifiedPrincipal
from account_migration import migrate,OwnerApproval
v=json.load(sys.stdin);p=VerifiedPrincipal(v['issuer'],v['subject'])
def stop(phase):
 if phase==v['phase']:os._exit(73)
migrate(v['plan'],p,OwnerApproval(p.issuer,p.subject,v['plan']['binding']),v['key'].encode(),checkpoint=stop)
