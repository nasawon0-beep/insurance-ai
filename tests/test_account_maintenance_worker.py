import io,json
from test_account_maintenance_adapter import setup

def test_worker_requests_key_only_after_valid_online_bound_approval(setup):
    import account_maintenance_worker as w
    m,plan,approval,execute,requests,vault=setup
    from test_account_maintenance_adapter import Online,URL
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from test_account_maintenance_adapter import b64
    # Separate synthetic signer; no test secret ever used in product config.
    signer=Ed25519PrivateKey.generate();claims=json.loads(__import__('base64').urlsafe_b64decode(approval()['payload']+'=='))
    raw=json.dumps(claims).encode();token={'kid':'review','payload':b64(raw),'sig':b64(signer.sign(raw))}
    frame={'plan':plan,'operation':'migrate','access_token':'synthetic-session','approval_token':token,
      'config':{'central_url':URL,'publishable_key':'sb_publishable_fixture','approval_keys':{'review':b64(signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))}},
      'installation_id':'1'*64,'client_epoch':4,'nonce':'2'*64,'confirmed':True}
    key=Fernet.generate_key().decode();output=io.StringIO()
    assert w.run(io.StringIO(json.dumps(frame)+'\n'+json.dumps({'key':key})+'\n'),output,now=1000,opener=Online())==0
    assert output.getvalue().splitlines()==['KEY_REQUIRED','DONE']
    assert key not in output.getvalue() and 'synthetic-session' not in output.getvalue()
    output=io.StringIO();frame['client_epoch']=5
    assert w.run(io.StringIO(json.dumps(frame)+'\n'),output,now=1000,opener=Online())==1
    assert output.getvalue().splitlines()==['FAILED']
