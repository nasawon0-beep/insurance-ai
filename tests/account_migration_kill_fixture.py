"""Synthetic subprocess fixture only. Parent kills this process at a recorded phase."""
import json,sys,time
from pathlib import Path
from account_workspace import VerifiedPrincipal
from account_migration import migrate,OwnerApproval
value=json.loads(sys.stdin.readline())
def checkpoint(phase):
    if phase==value['phase']:
        Path(value['marker']).write_text(phase)
        time.sleep(120)
migrate(value['plan'],VerifiedPrincipal(**value['principal']),OwnerApproval(**value['approval']),value['key'].encode(),checkpoint=checkpoint)
