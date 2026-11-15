"""Synthetic process-death fixture. No network, production roots or credentials."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local-engine'))
from account_workspace import VerifiedPrincipal
from account_migration import OwnerApproval,migrate
value=json.loads(Path(sys.argv[1]).read_text())
principal=VerifiedPrincipal(**value['principal'])
approval=OwnerApproval(principal.issuer,principal.subject,value['plan']['binding'])
def checkpoint(phase):
    if phase==value['failure']:os._exit(73)
migrate(value['plan'],principal,approval,Path(value['keyfile']).read_bytes(),checkpoint=checkpoint)
raise SystemExit('Failure checkpoint was not reached')
