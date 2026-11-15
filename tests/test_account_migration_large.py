"""64MiB synthetic payload per phase; OS process kill, NOT a hardware power failure."""
import hashlib,json,subprocess,sys,time
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from account_workspace import VerifiedPrincipal,workspace_for
from account_migration import plan_migration,migrate,rollback,restore_backup,OwnerApproval

def digest(path):
    with open(path,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

@pytest.mark.parametrize('phase',['backup','staging','promotion','committed'])
def test_large_synthetic_process_kill_retry_and_quarantine(tmp_path,phase):
    root=tmp_path/'한글 Install';root.mkdir();source=root/'legacy';source.mkdir()
    original=source/'synthetic.bin'
    with original.open('wb') as f:
        for i in range(64):f.write(bytes([i])*1048576)
    before=digest(original)
    a=VerifiedPrincipal('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    b=VerifiedPrincipal(a.issuer,'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    other=workspace_for(root,b).root;other.mkdir(parents=True);(other/'keep').write_bytes(b'OTHER ACCOUNT')
    plan=plan_migration(source,root,{'pdfs/synthetic.bin':'synthetic.bin'})
    approval=OwnerApproval(a.issuer,a.subject,plan['binding']);key=Fernet.generate_key();marker=root/'phase-marker'
    payload={'plan':plan,'principal':a.__dict__,'approval':approval.__dict__,'key':key.decode(),'phase':phase,'marker':str(marker)}
    start=time.monotonic()
    process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('account_migration_kill_fixture.py'))],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        process.stdin.write(json.dumps(payload).encode()+b'\n');process.stdin.close()
        deadline=time.monotonic()+60
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert marker.exists(),process.stderr.read().decode(errors='replace') if process.poll() is not None else 'checkpoint timeout'
        process.kill();process.wait(timeout=10);assert process.returncode!=0
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stderr.close()
    assert migrate(plan,a,approval,key)['phase']=='committed'
    assert digest(workspace_for(root,a).root/'pdfs/synthetic.bin')==before
    assert rollback(plan,a,approval,key)['phase']=='rolled_back'
    recovered=restore_backup(plan,a,approval,key)
    assert digest(recovered/'synthetic.bin')==before and digest(original)==before
    assert (other/'keep').read_bytes()==b'OTHER ACCOUNT'
    print(json.dumps({'phase':phase,'source_bytes':original.stat().st_size,'seconds':round(time.monotonic()-start,3),'killed_returncode':process.returncode}))
