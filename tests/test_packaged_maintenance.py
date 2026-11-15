"""Execute the shipping worker, not source Python; no live Auth/network or customer data."""
from pathlib import Path
import os,subprocess,json
from PyInstaller.archive.readers import CArchiveReader
ROOT=Path(__file__).resolve().parents[1]
EXE=ROOT/'desktop/src-tauri/binaries/maintenance/maintenance-worker.exe'
def test_shipping_worker_rejects_untrusted_frame_without_key(tmp_path):
    env={'SystemRoot':os.environ['SystemRoot'],'TEMP':str(tmp_path),'TMP':str(tmp_path)}
    result=subprocess.run([str(EXE)],input=b'{"unexpected":"synthetic-only"}\n',capture_output=True,env=env,timeout=30)
    assert result.returncode==1
    # Protocol moved to a dedicated native-captured channel; never parse stdout.
    assert result.stderr in (b'FAILED\n', b'FAILED\r\n')
    assert b'KEY_REQUIRED' not in result.stdout + result.stderr
    assert b'DONE' not in result.stdout + result.stderr
    assert not list(tmp_path.glob('**/customers.sqlite3'))
def test_shipping_archive_has_no_fixture_entry_or_signer():
    archive=CArchiveReader(str(EXE))
    assert 'account_maintenance_worker' in archive.toc
    assert not any('fixture' in name or 'signing_key' in name for name in archive.toc)
    modules=archive.open_embedded_archive('PYZ.pyz').toc
    assert 'account_migration' in modules and 'account_workspace' in modules
    assert not any('fixture' in name for name in modules)
