"""Explicit release gate; one fresh process and owned fixture directory per file."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Profiles are deliberate: only legacy positive customers receive account_bound_ci.
ENGINE = {
    'tests/test_account_backup_binding.py': 'independent',
    'tests/test_account_isolation_boundary.py': 'independent',
    'tests/test_account_maintenance_adapter.py': 'independent',
    'tests/test_account_maintenance_worker.py': 'independent',
    'tests/test_account_migration.py': 'independent',
    'tests/test_account_migration_large.py': 'independent',
    'tests/test_account_process_isolation.py': 'independent',
    'tests/test_account_storage_upgrade_boundary.py': 'independent',
    'tests/test_account_workspace.py': 'independent',
    'tests/test_audit.py': 'legacy-fixtures',
    'tests/test_customers_api.py': 'account-positive',
    'tests/test_engine_auth.py': 'legacy-fixtures',
    'tests/test_mobile_key_owner.py': 'independent',
    'tests/test_nsis_shortcut_name.py': 'independent',
    'tests/test_protected_business_key.py': 'independent',
    'tests/test_rag_encryption.py': 'independent',
    'tests/test_release_workflow_contract.py': 'independent',
    'tests/test_settings.py': 'legacy-fixtures',
    'tests/test_stt_router.py': 'legacy-fixtures',
    'tests/test_upgraded_business.py': 'independent',
    'tests/test_workspace_bootstrap_backup.py': 'independent',
    'tests/test_workspace_clock.py': 'independent',
    'tests/test_workspace_transition.py': 'independent',
    'tests/test_workspace_transition_interruption.py': 'independent',
    'tests/test_workspace_transition_rollback.py': 'independent',
    'tests/test_workspace_transition_safety.py': 'independent',
    'tests/test_release_blockers.py': 'independent',
}
CONTROL = {'control-server/tests/test_control.py': 'control'}
NODE = ['tests/windows-mobile-key-owner.test.mjs', 'tests/repository-public-maps.test.mjs']
REQUIRED = json.loads((ROOT / 'tests/release-regression-required.json').read_text(encoding='utf-8'))


def validate_selection(root=ROOT):
    selected = set(ENGINE) | set(CONTROL) | set(NODE)
    if not set(REQUIRED).issubset(selected):
        raise RuntimeError('CI_REQUIRED_SELECTION_MISSING')
    paths = [*ENGINE, *CONTROL, *NODE, 'tests/release-regression-required.json', 'tests/release_evidence.py', 'tests/release_sandbox/sitecustomize.py', 'scripts/create_release_only.py']
    for name in paths:
        if not (root / name).is_file():
            raise RuntimeError('CI_REQUIRED_INPUT_MISSING:' + name)
    assert ENGINE['tests/test_customers_api.py'] == 'account-positive'
    assert all(profile != 'account-positive' for name, profile in ENGINE.items() if name != 'tests/test_customers_api.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', choices=['engine', 'control'], required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    validate_selection()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='run-', dir=destination))
    rows = []
    for name, profile in (ENGINE if args.group == 'engine' else CONTROL).items():
        fixture = run / Path(name).stem
        fixture.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MOADOOM_ACCOUNT_', 'PYTEST_', 'CUSTOMER_', 'RAG_', 'CONTROL_', 'MAINTENANCE_TEST_', 'INSURANCE_AI_', 'VITE_MOADOOM_'))}
        # Clear inherited identity/secrets; known synthetic-only paths BEFORE import.
        env.update({'HOME':str(fixture), 'USERPROFILE':str(fixture), 'APPDATA':str(fixture/'appdata'), 'LOCALAPPDATA':str(fixture/'localappdata'),
                    'TEMP':str(fixture), 'TMP':str(fixture), 'TMPDIR':str(fixture), 'PYTHONDONTWRITEBYTECODE':'1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
                    'CUSTOMER_DB_PATH':str(fixture/'customers.sqlite3'), 'CUSTOMER_DB_KEYFILE':str(fixture/'customer-db.key'), 'CUSTOMER_DB_KEYFILE_ONLY':'1',
                    'RAG_DB_PATH':str(fixture/'rag.sqlite3'), 'INSURANCE_AI_API_SECRET_FILE':str(fixture/'api.secret'), 'COVERAGE_ANALYSIS_LOG_DIR':str(fixture/'logs'),
                    'CONTROL_DB_PATH':str(fixture/'control.sqlite3'), 'PYTHON_KEYRING_BACKEND':'keyring.backends.null.Keyring', 'DEV_SKIP_AUTH':'0',
                    'RELEASE_TEST_EVIDENCE':str(fixture/'test-ids.json'), 'RELEASE_REAL_HOME':str(Path.home()), 'MOADOOM_CI_FIXTURE_ROOT':str(fixture/'account-bound'), 'PYTHONPATH':os.pathsep.join([str(ROOT/'tests/release_sandbox'),str(ROOT/'tests'),str(ROOT/('control-server' if profile=='control' else 'local-engine')),str(ROOT)])})
        command = [sys.executable,'-m','pytest','-p','release_evidence','-p','no:cacheprovider','-v','--basetemp',str(fixture/'pytest')]
        if profile in ('independent','control'):
            command += ['--noconftest']
        if profile == 'account-positive':
            command += ['-p','account_bound_ci']
        command += [name]
        result = subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=600)
        (fixture/'pytest.log').write_text(result.stdout,encoding='utf-8')
        evidence = json.loads((fixture/'test-ids.json').read_text()) if (fixture/'test-ids.json').exists() else {}
        ok = result.returncode == 0 and bool(evidence.get('collected')) and not evidence.get('skipped') and set(evidence.get('collected',[])) == set(evidence.get('passed',[]))
        rows.append({'file':name,'profile':profile,'command':command,'returncode':result.returncode,'ok':ok,'evidence':evidence,'log':str(fixture/'pytest.log')})
        (run/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        print(name, 'PASS' if ok else 'FAIL', flush=True)
    if args.group == 'engine':
        node = shutil.which('node')
        version = subprocess.check_output([node, '--version'], text=True).strip()
        if not version.startswith('v24.'):
            raise RuntimeError('Release regression gate requires Node 24')
        (run/'node-version.txt').write_text(version,encoding='utf-8')
        for name in NODE:
            command=[node,'--test','--test-reporter=tap','--experimental-strip-types',name]
            result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=120)
            (run/(Path(name).stem+'.log')).write_text(result.stdout,encoding='utf-8')
            rows.append({'file':name,'command':command,'returncode':result.returncode,'ok':result.returncode==0 and '# skipped 0' in result.stdout})
            print(name,'PASS' if rows[-1]['ok'] else 'FAIL',flush=True)
    (run/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    print('Evidence:',run)
    if not all(row['ok'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
