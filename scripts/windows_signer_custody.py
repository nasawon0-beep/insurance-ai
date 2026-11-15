"""Windows signer custody. Never prints/exports private keys or reads stored credentials.
Operator tool; generation is explicit. Test keys cannot be uploaded to production.
DPAPI protects current-user scope, not a compromised user/admin/process memory.
"""
import argparse,base64,ctypes,hashlib,json,os,re,secrets,subprocess,sys
from ctypes import wintypes as W
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption,load_der_private_key
PROJECT='fhdushntttxbfwkxylhz'
FORMAT='moadoom-server-signer-dpapi-v1'

def sid():
    if os.name!='nt':raise ValueError('Windows required')
    value=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],creationflags=subprocess.CREATE_NO_WINDOW).decode('utf-8','replace')
    found=re.findall(r'S-1-5-\d+(?:-\d+)+',value)
    if len(found)!=1:raise ValueError('Cannot resolve current user SID')
    return found[0]

def safe_path(path):
    path=Path(os.path.abspath(path))
    for p in (path,*path.parents):
        if p.exists() or p.is_symlink():
            if p.lstat().st_file_attributes & 0x400:raise ValueError('Reparse points forbidden')
    return path

def secure_directory(path):
    path=safe_path(path)
    if path.exists():raise FileExistsError('Custody already exists; use verify/recover, never regenerate')
    if not path.parent.is_dir():raise ValueError('Parent must already exist')
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    convert=adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p];convert.restype=W.BOOL
    class SA(ctypes.Structure):_fields_=[('size',W.DWORD),('descriptor',ctypes.c_void_p),('inherit',W.BOOL)]
    descriptor=ctypes.c_void_p()
    if not convert('D:P(A;OICI;FA;;;'+sid()+')',1,ctypes.byref(descriptor),None):raise ValueError('ACL conversion failed')
    kernel.CreateDirectoryW.argtypes=[W.LPCWSTR,ctypes.POINTER(SA)];kernel.CreateDirectoryW.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    try:
        sa=SA(ctypes.sizeof(SA),descriptor,False)
        if not kernel.CreateDirectoryW(str(path),ctypes.byref(sa)):raise OSError('Exclusive protected directory creation failed')
    finally:kernel.LocalFree(descriptor)
    if not verify_acl(path):raise ValueError('Custody ACL verification failed; preserve directory')
    return path

def _acl_checks(sd,expected_sid,*,file=False):
    """Diagnostic only. Compare binary ACE/SID semantics; never authorizes access."""
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32')
    checks={name:False for name in ('descriptor_valid','dacl_present','dacl_nonnull','dacl_valid','control_valid','protected_or_file','single_ace','allow_ace','flags_expected','mask_expected','sid_valid','trustee_current','semantic_match')}
    def bind(name,args,result=W.BOOL):
        fn=getattr(adv,name);fn.argtypes=args;fn.restype=result;return fn
    valid=bind('IsValidSecurityDescriptor',[ctypes.c_void_p])
    checks['descriptor_valid']=bool(valid(sd))
    if not checks['descriptor_valid']:return checks
    present=W.BOOL();defaulted=W.BOOL();dacl=ctypes.c_void_p()
    get_dacl=bind('GetSecurityDescriptorDacl',[ctypes.c_void_p,ctypes.POINTER(W.BOOL),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(W.BOOL)])
    if not get_dacl(sd,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted)):return checks
    checks['dacl_present']=bool(present.value);checks['dacl_nonnull']=bool(dacl.value)
    if not all((checks['dacl_present'],checks['dacl_nonnull'])):return checks
    checks['dacl_valid']=bool(bind('IsValidAcl',[ctypes.c_void_p])(dacl))
    if not checks['dacl_valid']:return checks
    control=W.WORD();revision=W.DWORD()
    get_control=bind('GetSecurityDescriptorControl',[ctypes.c_void_p,ctypes.POINTER(W.WORD),ctypes.POINTER(W.DWORD)])
    checks['control_valid']=bool(get_control(sd,ctypes.byref(control),ctypes.byref(revision)))
    checks['protected_or_file']=bool(file or control.value & 0x1000)
    class ACL_INFO(ctypes.Structure):_fields_=[('count',W.DWORD),('used',W.DWORD),('free',W.DWORD)]
    info=ACL_INFO()
    get_info=bind('GetAclInformation',[ctypes.c_void_p,ctypes.c_void_p,W.DWORD,W.DWORD])
    if not get_info(dacl,ctypes.byref(info),ctypes.sizeof(info),2):return checks
    checks['single_ace']=info.count==1
    if not checks['single_ace']:return checks
    ace=ctypes.c_void_p()
    if not bind('GetAce',[ctypes.c_void_p,W.DWORD,ctypes.POINTER(ctypes.c_void_p)])(dacl,0,ctypes.byref(ace)):return checks
    class ACE(ctypes.Structure):_fields_=[('kind',W.BYTE),('flags',W.BYTE),('size',W.WORD),('mask',W.DWORD)]
    header=ctypes.cast(ace,ctypes.POINTER(ACE)).contents
    checks['allow_ace']=header.kind==0
    checks['flags_expected']=header.flags in ((0,0x10) if file else (0x03,))
    checks['mask_expected']=header.mask==0x1f01ff
    if not checks['allow_ace'] or header.size<16:return checks
    trustee=ctypes.c_void_p(ace.value+8)
    is_sid=bind('IsValidSid',[ctypes.c_void_p]);length=bind('GetLengthSid',[ctypes.c_void_p],W.DWORD)
    checks['sid_valid']=bool(is_sid(trustee))
    if not checks['sid_valid'] or 8+length(trustee)>header.size:
        checks['sid_valid']=False;return checks
    expected=ctypes.c_void_p();convert=bind('ConvertStringSidToSidW',[W.LPCWSTR,ctypes.POINTER(ctypes.c_void_p)])
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not convert(expected_sid,ctypes.byref(expected)):return checks
    try:
        equal=bind('EqualSid',[ctypes.c_void_p,ctypes.c_void_p])
        checks['trustee_current']=bool(equal(trustee,expected))
        owner=ctypes.c_void_p();owner_defaulted=W.BOOL()
        get_owner=bind('GetSecurityDescriptorOwner',[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(W.BOOL)])
        checks['owner_available']=bool(get_owner(sd,ctypes.byref(owner),ctypes.byref(owner_defaulted)) and owner.value)
        checks['owner_current']=bool(checks['owner_available'] and equal(owner,expected))
    finally:kernel.LocalFree(expected)
    required=('descriptor_valid','dacl_present','dacl_nonnull','dacl_valid','control_valid','protected_or_file','single_ace','allow_ace','flags_expected','mask_expected','sid_valid','trustee_current')
    checks['semantic_match']=all(checks[key] for key in required)
    return checks


def verify_acl(path,*,file=False):
    path=safe_path(path);adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32')
    get=adv.GetNamedSecurityInfoW
    get.argtypes=[W.LPWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)];get.restype=W.DWORD
    sd=ctypes.c_void_p();text=W.LPWSTR()
    convert=adv.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes=[ctypes.c_void_p,W.DWORD,W.DWORD,ctypes.POINTER(W.LPWSTR),ctypes.c_void_p];convert.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if get(str(path),1,4,None,None,None,None,ctypes.byref(sd)):raise ValueError('ACL query failed')
    try:
        if not convert(sd,1,4,ctypes.byref(text),None):raise ValueError('ACL inspection failed')
        serialized=text.value
        if serialized is None:raise ValueError('ACL inspection failed')
        current_sid=sid()
        pattern=(r'D:(?:P)?(?:AI)?\(A;(?:ID)?;FA;;;' if file else r'D:P(?:AI)?\(A;OICI;FA;;;')+re.escape(current_sid)+r'\)'
        matched=bool(re.fullmatch(pattern,serialized))
        if not matched:
            # Observation only: keep the existing fail-closed authorization decision.
            try:
                checks=_acl_checks(sd,current_sid,file=file)
                checks['serialized_trustee_numeric']=bool(re.search(r';;;S-',serialized))
                checks['legacy_match']=False
                print('CUSTODY_ACL_DIAGNOSTIC '+json.dumps(checks,sort_keys=True),file=sys.stderr)
            except Exception:
                print('CUSTODY_ACL_DIAGNOSTIC unavailable',file=sys.stderr)
        return matched
    finally:
        if text:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))
        kernel.LocalFree(sd)

def dpapi(data,context,protect):
    if os.name!='nt':raise ValueError('Windows DPAPI required')
    class Blob(ctypes.Structure):_fields_=[('size',W.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    def blob(v):
        b=(ctypes.c_ubyte*len(v)).from_buffer_copy(v);return Blob(len(v),b),b
    source,keep=blob(data);entropy,keep2=blob(hashlib.sha256((FORMAT+json.dumps(context,sort_keys=True)).encode()).digest());out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32')
    f=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    f.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,W.DWORD,ctypes.POINTER(Blob)];f.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not f(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(out)):raise ValueError('DPAPI verification failed')
    try:return ctypes.string_at(out.data,out.size)
    finally:kernel.LocalFree(ctypes.cast(out.data,ctypes.c_void_p))

def info_and_key(path,filename='signer.dpapi'):
    path=safe_path(path)
    if not verify_acl(path):raise ValueError('Custody ACL changed')
    file=safe_path(path/filename)
    if not verify_acl(file,file=True):raise ValueError('Private file ACL changed')
    if file.stat().st_size>32768:raise ValueError('Envelope too large')
    envelope=json.loads(file.read_bytes());info=envelope['info']
    if info['format']!=FORMAT:raise ValueError('Unknown format')
    raw=dpapi(base64.b64decode(envelope['blob'],validate=True),info,False)
    key=load_der_private_key(raw,None)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError('Wrong signing algorithm')
    public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
    if info['public']!={info['kid']:public}:raise ValueError('Public key mismatch')
    key.public_key().verify(key.sign(b'moadoom-custody-self-test-v1'),b'moadoom-custody-self-test-v1')
    return info,key

def load(path):return info_and_key(path)[1]

def create(path,purpose,kid,project,*,test_only=False,confirmed=False):
    if purpose not in ('workspace','maintenance') or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',kid):raise ValueError('Invalid purpose/KID')
    if test_only:
        if project!='fixture' or not kid.startswith('fixture-'):raise ValueError('Test keys require fixture identity')
    elif project!=PROJECT or not confirmed:raise ValueError('Explicit production custody approval required')
    path=secure_directory(path)
    key=Ed25519PrivateKey.generate()
    public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
    info={'format':FORMAT,'project':project,'purpose':purpose,'kid':kid,'test_only':test_only,'public':{kid:public},'version_reference':secrets.token_hex(16)}
    raw=key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())
    protected=dpapi(raw,info,True)
    if dpapi(protected,info,False)!=raw:raise ValueError('DPAPI readback failed')
    payload=json.dumps({'info':info,'blob':base64.b64encode(protected).decode()},sort_keys=True).encode()
    # Pending is always encrypted. Never remove on failure; recover this exact key.
    pending=path/'signer.pending.dpapi'
    with pending.open('xb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
    os.link(pending,path/'signer.dpapi')
    checked,_=info_and_key(path)
    with (path/'public.json').open('x',encoding='utf-8') as f:json.dump(checked,f,sort_keys=True)
    pending.unlink() # only encrypted, same-inode completed pending; no plaintext deletion claim
    return checked

def recover(path):
    path=safe_path(path)
    if not verify_acl(path):raise ValueError('Custody ACL changed')
    pending=safe_path(path/'signer.pending.dpapi');final=path/'signer.dpapi'
    if not final.exists():
        # Authenticate exact encrypted pending before promotion; never generate another key.
        info_and_key(path,filename='signer.pending.dpapi')
        os.link(pending,final)
    info,_=info_and_key(path)
    public=path/'public.json'
    if public.exists():
        if json.loads(public.read_bytes())!=info:raise ValueError('Public metadata conflict; preserve artifacts')
    else:
        with public.open('x',encoding='utf-8') as f:json.dump(info,f,sort_keys=True)
    if pending.exists():
        if not os.path.samefile(pending,final):raise ValueError('Pending is not the promoted file; preserve both')
        pending.unlink()
    return info

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['create','verify','recover']);p.add_argument('--directory',required=True);p.add_argument('--purpose',choices=['workspace','maintenance']);p.add_argument('--kid');p.add_argument('--project',default=PROJECT);p.add_argument('--test-only',action='store_true');p.add_argument('--confirm-production',action='store_true');a=p.parse_args()
    try:
        result=create(a.directory,a.purpose,a.kid,a.project,test_only=a.test_only,confirmed=a.confirm_production) if a.action=='create' else recover(a.directory) if a.action=='recover' else info_and_key(a.directory)[0]
        print(json.dumps(result,sort_keys=True))
    except Exception:
        print('Custody operation failed; preserve encrypted artifacts; no key regenerated.',file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
