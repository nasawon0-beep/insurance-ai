"""Synthetic-only maintenance authority; no deployed issuer or native transport claim."""
import base64
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.fernet import Fernet
from account_migration import plan_migration, MigrationError
from account_workspace import VerifiedPrincipal, workspace_for

A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
URL='https://fixture.supabase.co'

def b64(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode()

class Online:
    def __init__(self, subject=A, rejected=False): self.subject=subject;self.rejected=rejected;self.calls=0
    def open(self, request, timeout):
        self.calls+=1
        if self.rejected: raise OSError('synthetic rejection')
        return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    status=200
    def read(self,limit):return json.dumps({'id':self.subject}).encode()

@pytest.fixture
def setup(tmp_path):
    import account_maintenance as m
    source=tmp_path/'legacy';source.mkdir();(source/'original').write_bytes(b'ONLY SYNTHETIC')
    plan=plan_migration(source,tmp_path,{'pdfs/original':'original'})
    signer=Ed25519PrivateKey.generate()
    config=m.MaintenanceConfig(URL,'sb_publishable_fixture',{'review':b64(signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))})
    def approval(**changes):
        claims=dict(v=2,kid='review',iss=URL+'/auth/v1',sub=A,aud='moadoom-maintenance',purpose='legacy-owner-approval',binding=plan['binding'],operation='migrate',iat=1000,exp=1300,installation_id='1'*64,client_epoch=4,nonce='2'*64,approval_id='cccccccc-cccc-4ccc-8ccc-cccccccccccc')
        claims.update(changes); raw=json.dumps(claims,separators=(',',':')).encode()
        return dict(kid='review',payload=b64(raw),sig=b64(signer.sign(raw)))
    vault={};requests=[]
    def key_vault(principal,binding,create):
        requests.append((principal,binding,create));tag=(principal,binding)
        if tag not in vault:
            if not create:raise MigrationError('Missing protected recovery key')
            vault[tag]=Fernet.generate_key()
        return vault[tag]
    def execute(operation='migrate',token=None,transport=None,now=1000):
        return m.execute_reviewed_migration(plan,operation,'synthetic-session',token or approval(operation=operation),config,key_vault,installation_id='1'*64,client_epoch=4,nonce='2'*64,confirmed=True,now=now,opener=transport or Online())
    return m,plan,approval,execute,requests,vault

def test_online_signed_plan_approval_then_key_vault_and_quarantine_recovery(setup):
    m,plan,approval,execute,requests,vault=setup
    assert execute()['phase']=='committed'
    assert execute()['phase']=='committed' # exact reviewed operation may resume safely
    assert execute('rollback')['phase']=='rolled_back'
    recovered=execute('restore_backup')
    assert (recovered/'original').read_bytes()==b'ONLY SYNTHETIC'
    assert len(vault)==1
    assert [r[2] for r in requests]==[True,True,False,False]
    assert not workspace_for(plan['installation'],VerifiedPrincipal(URL+'/auth/v1',A)).root.exists()

@pytest.mark.parametrize('change',[
    {'sub':B},{'iss':'https://other.supabase.co/auth/v1'}, {'aud':'moadoom-desktop'},
    {'purpose':'account-workspace'}, {'binding':'f'*64}, {'operation':'rollback'},
    {'iat':1001},{'exp':1000},{'exp':1301},{'iat':True},{'v':1},{'unreviewed':True},
    {'installation_id':'f'*64},{'client_epoch':5},{'client_epoch':True},{'nonce':'f'*64},{'approval_id':'invalid'},
])
def test_signed_but_wrong_scope_or_time_never_reaches_key_or_writes(setup,change):
    m,plan,approval,execute,requests,vault=setup
    with pytest.raises(MigrationError):execute(token=approval(**change))
    assert requests==[] and vault=={}

@pytest.mark.parametrize('case',['tampered','unknown-key','wrong-account','online-rejected','expired','missing-signature'])
def test_invalid_authority_is_fail_closed_before_key_access(setup,case):
    m,plan,approval,execute,requests,vault=setup
    token=approval();online=Online();now=1000
    if case=='tampered':token['payload']=b64(b'{}')
    if case=='unknown-key':token['kid']='unknown'
    if case=='wrong-account':online=Online(B)
    if case=='online-rejected':online=Online(rejected=True)
    if case=='expired':now=1300
    if case=='missing-signature':token.pop('sig')
    with pytest.raises(MigrationError):execute(token=token,transport=online,now=now)
    assert not requests and not vault

def test_recovery_never_creates_missing_key(setup):
    m,plan,approval,execute,requests,vault=setup
    with pytest.raises(MigrationError,match='Missing protected recovery key'):execute('restore_backup')
    assert requests[0][2] is False and not vault

def test_changed_manifest_never_reaches_vault(setup):
    m,plan,approval,execute,requests,vault=setup
    plan['files']['pdfs/original']['sha256']='f'*64
    with pytest.raises(MigrationError):execute()
    assert not requests
