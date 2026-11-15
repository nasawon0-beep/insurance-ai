"""Native-owned anonymous stdin/stdout pipe worker. Never an HTTP endpoint.

Only the trusted native launcher supplies config/frame after pinned-artifact and
live epoch checks. Credentials/backup key never appear in argv/env/file/logs.
No plaintext export or portable recovery policy is introduced here.
"""
import json
import sys
from account_maintenance import MaintenanceConfig,execute_reviewed_migration,_unique_object
from account_migration import MigrationError

def _frame(stream,limit):
    raw=stream.readline(limit+1)
    if len(raw)>limit or not raw.endswith('\n'):raise MigrationError('Invalid pipe frame')
    return json.loads(raw,object_pairs_hook=_unique_object)

def run(input_stream,output_stream,*,now=None,opener=None):
    def signal(value):output_stream.write(value+'\n');output_stream.flush()
    try:
        frame=_frame(input_stream,1048576)
        if set(frame)!={'plan','operation','access_token','approval_token','config','installation_id','client_epoch','nonce','confirmed'}:
            raise MigrationError('Unexpected worker frame')
        config=MaintenanceConfig(**frame.pop('config'))
        def key_vault(principal,binding,create):
            signal('KEY_REQUIRED')
            value=_frame(input_stream,128)
            if set(value)!={'key'} or not isinstance(value['key'],str):raise MigrationError('Invalid key frame')
            return value['key'].encode('ascii')
        execute_reviewed_migration(**frame,config=config,key_vault=key_vault,now=now,opener=opener)
        signal('DONE');return 0
    except Exception:
        # Never serialize exceptions: network errors may carry credentials or paths.
        signal('FAILED');return 1

if __name__=='__main__':
    if len(sys.argv)!=1 or sys.stdin.isatty():sys.exit(2)
    sys.stdin.reconfigure(encoding='utf-8',errors='strict')
    # Dedicated native-captured status channel. stdout is never protocol data.
    sys.stderr.reconfigure(encoding='utf-8',errors='strict')
    sys.exit(run(sys.stdin,sys.stderr))
