"""Build only the maintenance dependency closure, not the engine's shadowing packages."""
from pathlib import Path
import os,sys,tempfile,shutil,subprocess,hashlib,json
repo=Path(__file__).resolve().parents[1]
work=Path(tempfile.mkdtemp(prefix='maintenance-package-',dir=os.environ['TMPDIR']))
names=['account_maintenance_worker.py','account_maintenance.py','account_migration.py','account_workspace.py','protected_business_key.py','database/crypto.py','rag/store.py','maintenance-worker.spec']
for name in names:
    (work/name).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(repo/'local-engine'/name,work/name)
# Maintenance imports storage primitives only, not the engine HTTP/router package initializers.
for package in ['database','rag']:
    (work/package/'__init__.py').write_text('',encoding='utf-8')
qa=sys.argv[1:]==['--qa-fixture']
if sys.argv[1:] and not qa:raise SystemExit('Only --qa-fixture is supported')
if qa:
    shutil.copy2(repo/'tests/native_maintenance_fixture.py',work/'native_maintenance_fixture.py')
    spec=work/'maintenance-worker.spec'
    spec.write_text(spec.read_text().replace("['account_maintenance_worker.py']","['native_maintenance_fixture.py']").replace("name='maintenance-worker'","name='qa-maintenance-worker'"))
out=Path('C:/build/moadoom-maintenance-qa') if qa else repo/'desktop/src-tauri/binaries/maintenance'
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--workpath',str(work/'build'),'--distpath',str(out),'maintenance-worker.spec'],cwd=work,check=True,timeout=600)
p=out/('qa-maintenance-worker.exe' if qa else 'maintenance-worker.exe')
if not qa:
    digest=hashlib.sha256(b''.join(name.encode()+b'\0'+(work/name).read_bytes()+b'\0' for name in names)).hexdigest()
    (out/'source.sha256').write_text(digest,encoding='ascii')
print(json.dumps({'executable':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'work':str(work)}))
