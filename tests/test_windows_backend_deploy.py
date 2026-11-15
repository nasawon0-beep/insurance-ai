"""Synthetic transport only: no token, credentials, network or hosted writes."""
import importlib.util,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

def module():
    path=ROOT/'scripts/windows_backend_deploy.py'
    assert path.is_file(), 'Protected Windows backend deploy tool missing'
    spec=importlib.util.spec_from_file_location('deploy',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_existing_secret_refused_without_write(tmp_path):
    m=module()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    info={'project':m.PROJECT,'test_only':False,'purpose':'workspace','kid':'synthetic-only','version_reference':'synthetic'}
    calls=[]
    def request(method,path,body=None):
        calls.append(method)
        return [{'name':'MOADOOM_WORKSPACE_KID','value':'digest-only'}]
    with pytest.raises(ValueError):m.publish(tmp_path,info,Ed25519PrivateKey.generate(),request)
    assert calls==['GET']

def test_upload_readback_and_no_retry_after_ambiguous_write(tmp_path):
    m=module()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import hashlib
    info={'project':m.PROJECT,'test_only':False,'purpose':'maintenance','kid':'synthetic-only','version_reference':'synthetic'}
    key=Ed25519PrivateKey.generate();state={};calls=[]
    def request(method,path,body=None):
        calls.append(method)
        if method=='POST':
            state.update({v['name']:hashlib.sha256(v['value'].encode()).hexdigest() for v in body});return None
        return [{'name':k,'value':v} for k,v in state.items()]
    result=m.publish(tmp_path,info,key,request)
    assert calls==['GET','POST','GET'] and result['secret_digest_verified'] and not result['issuance_verified']
    assert m.verify_remote(info,key,request)==result
    with pytest.raises(ValueError):m.publish(tmp_path,info,key,request)
    assert calls.count('POST')==1
    # Empty remote readback after ambiguous POST: journal still prohibits a second POST.
    state.clear()
    with pytest.raises(FileExistsError):m.publish(tmp_path,info,key,request)
    assert calls.count('POST')==1

def test_fixture_key_rejected_before_network(tmp_path):
    m=module()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    def denied(*args):raise AssertionError('Network was called')
    with pytest.raises(ValueError):m.publish(tmp_path,{'project':'fixture','test_only':True},Ed25519PrivateKey.generate(),denied)
