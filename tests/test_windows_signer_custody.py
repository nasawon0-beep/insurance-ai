"""Real Windows DPAPI/ACL test keys only; never contacts Supabase."""
import importlib.util,json,sys
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/windows_signer_custody.py'

def module():
    assert SCRIPT.is_file(), 'Windows protected signer custody helper is missing'
    spec=importlib.util.spec_from_file_location('custody',SCRIPT)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def test_dpapi_signer_roundtrip_and_no_plaintext(tmp_path):
    m=module();d=tmp_path/'custody'
    info=m.create(d,'workspace','fixture-workspace-1','fixture',test_only=True)
    assert info['test_only'] is True
    key=m.load(d)
    from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
    raw=key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())
    import base64
    for p in d.iterdir():
        assert raw not in p.read_bytes()
        assert base64.b64encode(raw) not in p.read_bytes()
    key.public_key().verify(key.sign(b'fixture'),b'fixture')
    assert m.verify_acl(d)
    before={p.name:p.read_bytes() for p in d.iterdir()}
    with pytest.raises((ValueError,FileExistsError,OSError)):
        m.create(d,'workspace','fixture-workspace-1','fixture',test_only=True)
    assert before=={p.name:p.read_bytes() for p in d.iterdir()}

def test_interrupted_publish_recovers_same_encrypted_key(tmp_path,monkeypatch):
    m=module();d=tmp_path/'custody'
    link=m.os.link
    monkeypatch.setattr(m.os,'link',lambda *args:(_ for _ in ()).throw(OSError('synthetic interruption')))
    with pytest.raises(OSError):m.create(d,'maintenance','fixture-maintenance-1','fixture',test_only=True)
    pending=(d/'signer.pending.dpapi').read_bytes()
    monkeypatch.setattr(m.os,'link',link)
    assert hasattr(m,'recover'), 'Interrupted encrypted custody needs explicit recovery'
    recovered=m.recover(d)
    assert recovered['purpose']=='maintenance'
    assert (d/'signer.dpapi').read_bytes()==pending
    assert not (d/'signer.pending.dpapi').exists()
    assert m.recover(d)==recovered

def test_file_acl_widening_is_refused(tmp_path):
    m=module();d=tmp_path/'custody'
    m.create(d,'workspace','fixture-workspace-1','fixture',test_only=True)
    import subprocess
    subprocess.run(['icacls',str(d/'signer.dpapi'),'/grant','*S-1-1-0:R'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    with pytest.raises(ValueError,match='ACL'):m.load(d)

@pytest.mark.parametrize('change',['project','blob'])
def test_dpapi_rejects_tampering_and_fixture_relabel(tmp_path,change):
    m=module();d=tmp_path/'custody'
    m.create(d,'workspace','fixture-workspace-1','fixture',test_only=True)
    p=d/'signer.dpapi';envelope=json.loads(p.read_bytes())
    if change=='project':envelope['info'].update(project=m.PROJECT,test_only=False)
    else:envelope['blob']='AAAA'
    p.write_text(json.dumps(envelope),encoding='utf-8')
    with pytest.raises(ValueError):m.load(d)

def test_concurrent_creation_has_one_winner(tmp_path):
    m=module();d=tmp_path/'custody'
    from concurrent.futures import ThreadPoolExecutor
    def attempt(_):
        try:return m.create(d,'workspace','fixture-workspace-1','fixture',test_only=True)
        except OSError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert sum(x is not None for x in results)==1
    assert m.info_and_key(d)[0]==next(x for x in results if x is not None)
