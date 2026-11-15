"""Versioned Windows-user DPAPI business key envelope.
Identity is supplied only by the verified native launcher/approved maintenance worker.
DPAPI is not a defense against arbitrary code running as the same Windows user.
Legacy conversion is explicit, to a different destination, and never deletes source.
"""
import base64,ctypes,hashlib,json,os,re,secrets
from pathlib import Path
from uuid import UUID
FORMAT='moadoom-business-key'

def identity(issuer,subject):
    if not isinstance(issuer,str) or not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co/auth/v1',issuer) or not isinstance(subject,str) or str(UUID(subject))!=subject:
        raise ValueError('Verified immutable business-key owner required')
    return [issuer,subject]

def environment_identity():
    issuer=os.environ.get('MOADOOM_ACCOUNT_ISSUER');subject=os.environ.get('MOADOOM_ACCOUNT_SUBJECT')
    return identity(issuer,subject) if issuer or subject else None

def _dpapi(data,owner,protect):
    if os.name!='nt':raise ValueError('Windows DPAPI required; no plaintext fallback')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    def blob(value):
        buffer=(ctypes.c_ubyte*len(value)).from_buffer_copy(value)
        return Blob(len(value),buffer),buffer
    source,keep=blob(data)
    entropy,keep_entropy=blob(hashlib.sha256(b'moadoom-business-key:v1:'+json.dumps(owner,separators=(',',':')).encode()).digest())
    output=Blob();crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(output)):
        raise ValueError('Business-key OS protection/owner verification failed')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(ctypes.cast(output.data,ctypes.c_void_p))

def encode_key(key,issuer,subject):
    owner=identity(issuer,subject)
    if len(key)!=32:raise ValueError('Invalid business-key length')
    plain=json.dumps({'owner':owner,'key':base64.b64encode(key).decode()},separators=(',',':')).encode()
    return json.dumps({'format':FORMAT,'version':1,'protection':'windows-user-dpapi','owner':owner,'blob':base64.b64encode(_dpapi(plain,owner,True)).decode()},sort_keys=True).encode()

def decode_key(data,issuer,subject):
    owner=identity(issuer,subject)
    try:
        value=json.loads(data)
        if value.get('format')!=FORMAT or value.get('version')!=1 or value.get('protection')!='windows-user-dpapi' or value.get('owner')!=owner:raise ValueError()
        plain=json.loads(_dpapi(base64.b64decode(value['blob'],validate=True),owner,False))
        key=base64.b64decode(plain['key'],validate=True)
        if plain['owner']!=owner or len(key)!=32:raise ValueError()
        return key
    except Exception as error:
        raise ValueError('Protected business key invalid; legacy keys require explicit approved conversion, never fallback/new-key replacement') from error

def read_protected(path,issuer,subject):
    path=Path(path)
    if path.stat().st_size>16384:raise ValueError('Business key envelope too large')
    return decode_key(path.read_bytes(),issuer,subject)

def write_protected(path,key,issuer,subject):
    path=Path(path);data=encode_key(key,issuer,subject)
    # Verify OS readback before publishing, and never overwrite a concurrent winner.
    if decode_key(data,issuer,subject)!=key:raise ValueError('Business key verification failed')
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_name(path.name+'.pending-'+secrets.token_hex(12))
    try:
        with pending.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.link(pending,path)
    finally:
        if pending.exists():pending.unlink()

def convert_legacy(source,destination,issuer,subject,*,confirmed):
    source=Path(source);destination=Path(destination)
    if not confirmed or source.resolve()==destination.resolve():raise ValueError('Explicit separate-destination conversion required')
    if source.stat().st_size>1024:raise ValueError('Invalid legacy key')
    key=base64.b64decode(source.read_bytes().strip(),validate=True)
    if len(key)!=32:raise ValueError('Invalid legacy key')
    write_protected(destination,key,issuer,subject)
