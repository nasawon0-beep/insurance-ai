"""Operator-only in-memory signer delivery. No credential discovery or plaintext temp files.
Requires an approved exclusive change window: Supabase secret POST is upsert, not CAS.
Never use if another operator/automation can write these same secret names concurrently.
"""
import argparse,base64,getpass,hashlib,json,os,ssl,sys,urllib.request
from pathlib import Path
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
from windows_signer_custody import PROJECT,info_and_key

def payload(info,key):
    if info.get('test_only') is not False or info.get('project')!=PROJECT or info.get('purpose') not in ('workspace','maintenance'):raise ValueError('Production delivery refuses fixture/wrong project keys')
    prefix='MOADOOM_'+info['purpose'].upper()
    return [{'name':prefix+'_KID','value':info['kid']},{'name':prefix+'_SIGNING_KEY','value':base64.b64encode(key.private_bytes(Encoding.DER,PrivateFormat.PKCS8,NoEncryption())).decode()}]

def verify_remote(info,key,request):
    values=payload(info,key);names={x['name']:hashlib.sha256(x['value'].encode()).hexdigest() for x in values}
    rows=request('GET',f'/v1/projects/{PROJECT}/secrets')
    remote={x['name']:x['value'] for x in rows if x['name'] in names}
    if remote!=names:raise ValueError('Secret digest verification failed; preserve custody and do not retry upload')
    return {'project':PROJECT,'purpose':info['purpose'],'version_reference':info['version_reference'],'secret_digest_verified':True,'issuance_verified':False}

def publish(directory,info,key,request):
    values=payload(info,key);names={x['name'] for x in values}
    rows=request('GET',f'/v1/projects/{PROJECT}/secrets')
    if any(x['name'] in names for x in rows):raise ValueError('Existing signer secret found; overwrite forbidden')
    # Journal before the only write: interruption never leads to automatic retry/regeneration.
    with (Path(directory)/'upload-intent.json').open('x',encoding='utf-8') as f:
        json.dump({'project':PROJECT,'purpose':info['purpose'],'version_reference':info['version_reference'],'state':'attempted-verify-before-any-retry'},f);f.flush();os.fsync(f.fileno())
    request('POST',f'/v1/projects/{PROJECT}/secrets',values)
    return verify_remote(info,key,request)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Credential-bearing redirect refused')

def transport(token):
    # TLS verification enabled; no proxy credentials, debug, body logging, retry or redirect.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def request(method,path,body=None):
        if path!=f'/v1/projects/{PROJECT}/secrets' or method not in ('GET','POST'):raise ValueError('Endpoint outside scope')
        req=urllib.request.Request('https://api.supabase.com'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with opener.open(req,timeout=30) as r:
                raw=r.read(1048577)
                if len(raw)>1048576:raise ValueError('Oversized metadata response')
                return json.loads(raw) if raw else None
        except Exception:raise ValueError('Management request failed; response body suppressed; inspect metadata before retry') from None
    return request

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['upload','verify-upload']);p.add_argument('--directory',required=True);p.add_argument('--exclusive-change-window-approved',action='store_true');a=p.parse_args()
    try:
        info,key=info_and_key(a.directory)
        payload(info,key) # Refuse test keys before credential prompt/network.
        if not a.exclusive_change_window_approved or not sys.stdin.isatty():raise ValueError('Approved exclusive window and non-recorded interactive operator console required')
        token=getpass.getpass('Operator only: approved scoped Supabase token (hidden, not saved): ')
        if not token or '\r' in token or '\n' in token or len(token)>8192:raise ValueError('Invalid credential input')
        request=transport(token)
        result=publish(a.directory,info,key,request) if a.action=='upload' else verify_remote(info,key,request)
        print(json.dumps(result,sort_keys=True))
    except Exception:
        print('Delivery stopped. No automatic retry/delete/overwrite. Keep encrypted custody; verify metadata with operator.',file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
