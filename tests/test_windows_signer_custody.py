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


@pytest.mark.parametrize('case,accepted',[
    ('current-directory',True),('alias500-directory',True),('alias501-directory',True),
    ('current-file',True),('inherited-file',True),('alias500-file',True),
    ('foreign-admin',False),('foreign-everyone',False),('extra-ace',False),
    ('read-only',False),('deny-ace',False),('inherit-only',False),
    ('no-propagate',False),('wrong-inheritance',False),('unprotected-directory',False),
    ('null-dacl',False),('empty-dacl',False),('missing-dacl',False),
    ('propagating-file',False),('inherited-directory',False),('pending-inheritance',False),('foreign-user',False),
])
def test_binary_dacl_policy(case,accepted):
    # Real Windows descriptors in memory; no foreign-account directory or key created.
    import ctypes
    from ctypes import wintypes as W
    m=module();expected=m.sid();file=case.endswith('file')
    if case.startswith('alias500'):expected=expected.rsplit('-',1)[0]+'-500'
    if case.startswith('alias501'):expected=expected.rsplit('-',1)[0]+'-501'
    trustee=expected;flags='ID' if case=='inherited-file' else '' if file else 'OICI'
    rights='FA';kind='A';prefix='D:P'
    if case=='foreign-admin':trustee='BA'
    if case=='foreign-everyone':trustee='WD'
    if case=='foreign-user':trustee=expected.rsplit('-',1)[0]+'-4294967294'
    if case=='propagating-file':flags='OICI'
    if case=='inherited-directory':flags='OICIID'
    if case=='pending-inheritance':prefix='D:PAR'
    if case=='read-only':rights='FR'
    if case=='deny-ace':kind='D'
    if case=='inherit-only':flags='OICIIO'
    if case=='no-propagate':flags='OICINP'
    if case=='wrong-inheritance':flags='OI'
    if case=='unprotected-directory':prefix='D:'
    sddl=prefix+'('+kind+';'+flags+';'+rights+';;;'+trustee+')'
    if case=='extra-ace':sddl+='(A;OICI;FR;;;WD)'
    if case=='null-dacl':sddl='D:NO_ACCESS_CONTROL'
    if case=='empty-dacl':sddl='D:P'
    if case=='missing-dacl':sddl='O:'+expected
    adv=ctypes.WinDLL('advapi32');kernel=ctypes.WinDLL('kernel32')
    convert=adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p];convert.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];sd=ctypes.c_void_p()
    converted=bool(convert(sddl,1,ctypes.byref(sd),None));assert converted
    try:
        checks=m._acl_checks(sd,expected,file=file)
        assert checks['semantic_match'] is accepted
        assert all(type(value) is bool for value in checks.values())
    finally:kernel.LocalFree(sd)


@pytest.mark.parametrize('principal',['WD','BA'])
def test_directory_acl_widening_is_refused(tmp_path,principal):
    m=module();d=tmp_path/'custody'
    m.create(d,'workspace','fixture-widening','fixture',test_only=True)
    import subprocess
    trustee={'WD':'*S-1-1-0:R','BA':'*S-1-5-32-544:R'}[principal]
    result=subprocess.run(['icacls',str(d),'/grant',trustee],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert result.returncode==0
    with pytest.raises(ValueError,match='ACL'):m.load(d)


@pytest.mark.parametrize('inspection',['candidate-match','inspection-error'])
def test_diagnostic_never_authorizes_or_discloses_values(tmp_path,monkeypatch,capsys,inspection):
    m=module();d=tmp_path/'custody';m.secure_directory(d)
    import subprocess
    result=subprocess.run(['icacls',str(d),'/grant','*S-1-1-0:R'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert result.returncode==0
    secret='diagnostic-canary-not-for-logs'
    if inspection=='inspection-error':
        def inspect(*args,**kwargs):raise ValueError(secret)
        monkeypatch.setattr(m,'_acl_checks',inspect)
    else:
        emit=m._acl_diagnostic
        def diagnostic(checks):
            emit(checks)
            return True  # A logging result must not override a rejected binary ACL.
        monkeypatch.setattr(m,'_acl_diagnostic',diagnostic)
    matched=m.verify_acl(d)
    output=capsys.readouterr().err
    assert matched is False
    assert 'CUSTODY_ACL_DIAGNOSTIC' in output
    assert secret not in output and str(d) not in output and 'S-1-' not in output
    if inspection=='candidate-match':
        data=json.loads(output.split('CUSTODY_ACL_DIAGNOSTIC ',1)[1])
        assert data['semantic_match'] is False
        assert all(type(value) is bool for value in data.values())
    else:assert output.strip()=='CUSTODY_ACL_DIAGNOSTIC unavailable'


@pytest.mark.parametrize('rid',[500,501])
@pytest.mark.parametrize('file',[False,True])
def test_verify_binary_alias_descriptor(tmp_path,monkeypatch,rid,file):
    import ctypes
    from ctypes import wintypes as W
    m=module();expected=m.sid().rsplit('-',1)[0]+'-'+str(rid)
    native=m.ctypes.WinDLL;adv=native('advapi32')
    convert=adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p];convert.restype=W.BOOL
    def query(path,kind,info,*outputs):
        # Transport seam only: descriptor allocation and all binary checks are real WinAPI.
        sddl='D:P(A;'+('' if file else 'OICI')+';FA;;;'+expected+')'
        return 0 if convert(sddl,1,outputs[-1],None) else 1
    class Proxy:
        def __getattr__(self,name):return query if name=='GetNamedSecurityInfoW' else getattr(adv,name)
    monkeypatch.setattr(m.ctypes,'WinDLL',lambda name,*a,**kw:Proxy() if name=='advapi32' else native(name,*a,**kw))
    monkeypatch.setattr(m,'sid',lambda:expected)
    accepted=m.verify_acl(tmp_path,file=file)
    assert accepted is True


@pytest.mark.parametrize('fault',[
    'GetNamedSecurityInfoW','IsValidSecurityDescriptor','GetSecurityDescriptorDacl',
    'IsValidAcl','GetSecurityDescriptorControl','GetAclInformation','GetAce',
    'IsValidSid','GetLengthSid','ConvertStringSidToSidW','EqualSid',
    'ace-null','ace-outside','ace-truncated','sid-overrun',
    'descriptor-null','expected-sid-null','acl-info-oversize',
])
def test_acl_api_and_memory_boundaries_fail_closed(tmp_path,monkeypatch,fault):
    import ctypes
    m=module();d=m.secure_directory(tmp_path/'custody')
    native=m.ctypes.WinDLL;adv=native('advapi32')
    original_ace=adv.GetAce
    from ctypes import wintypes as W
    original_ace.argtypes=[ctypes.c_void_p,W.DWORD,ctypes.POINTER(ctypes.c_void_p)];original_ace.restype=W.BOOL
    def broken(*args):
        if fault=='GetNamedSecurityInfoW':return 5
        if fault=='descriptor-null':return 0
        if fault=='expected-sid-null':return 1
        if fault=='acl-info-oversize':
            fields=ctypes.cast(args[1],ctypes.POINTER(W.DWORD))
            fields[0]=1;fields[1]=0xffffffff;fields[2]=0
            return 1
        if fault.startswith('ace-') or fault=='sid-overrun':
            target=ctypes.cast(args[-1],ctypes.POINTER(ctypes.c_void_p))
            if fault=='ace-null':target[0]=None;return 1
            if fault=='ace-outside':target[0]=1;return 1
            status=original_ace(*args)
            if not status:return status
            if fault=='ace-truncated':ctypes.c_ushort.from_address(target[0]+2).value=8
            else:ctypes.c_ubyte.from_address(target[0]+9).value=255
            return status
        return 0
    api='GetAce' if fault.startswith('ace-') or fault=='sid-overrun' else fault
    api={'descriptor-null':'GetNamedSecurityInfoW','expected-sid-null':'ConvertStringSidToSidW','acl-info-oversize':'GetAclInformation'}.get(api,api)
    class Proxy:
        def __getattr__(self,name):return broken if name==api else getattr(adv,name)
    monkeypatch.setattr(m.ctypes,'WinDLL',lambda name,*a,**kw:Proxy() if name=='advapi32' else native(name,*a,**kw))
    try:accepted=m.verify_acl(d)
    except ValueError:accepted=False
    assert accepted is False
