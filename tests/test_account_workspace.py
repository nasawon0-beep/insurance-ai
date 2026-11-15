import hashlib,json,sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from account_boundary import AccountBinding,AccountBoundaryMiddleware
from account_workspace import verify_central_identity,workspace_for,IdentityUnavailable

A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ISSUER='https://fixture.supabase.co/auth/v1'

class Response:
    status=200
    def __init__(self,value):self.value=value
    def read(self,n):return json.dumps(self.value).encode()[:n]
    def __enter__(self):return self
    def __exit__(self,*args):pass
class Transport:
    def __init__(self,value):self.value=value;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        if isinstance(self.value,Exception):raise self.value
        return Response(self.value)
def verified(subject):
    return verify_central_identity('https://fixture.supabase.co','sb_publishable_fixture','synthetic-token',opener=Transport({'id':subject}))

def test_online_identity_uses_subject_not_email_and_refuses_offline():
    transport=Transport({'id':A,'email':'not-an-authority@example.invalid'})
    principal=verify_central_identity('https://fixture.supabase.co','sb_publishable_fixture','synthetic-token',opener=transport)
    assert principal.subject==A
    assert transport.requests[0].full_url=='https://fixture.supabase.co/auth/v1/user'
    for value in [{'email':'not-an-authority@example.invalid'},{'id':'../other'},OSError('offline')]:
        with pytest.raises(IdentityUnavailable):
            verify_central_identity('https://fixture.supabase.co','sb_publishable_fixture','synthetic-token',opener=Transport(value))

@pytest.mark.parametrize('url',['http://fixture.supabase.co','https://evil.invalid','https://fixture.supabase.co/redirect'])
def test_identity_configuration_cannot_redirect_authority(url):
    with pytest.raises(IdentityUnavailable):verify_central_identity(url,'sb_publishable_fixture','token')

def test_two_installation_roots_and_accounts_never_resolve_legacy(tmp_path):
    # Storage primitive test, NOT a native process lifecycle / full-app test.
    for installation in ['installation-one','installation-two']:
        base=tmp_path/installation;base.mkdir()
        legacy=base/'customers.sqlite3';legacy.write_bytes(b'UNASSIGNED-LEGACY')
        for subject in [A,B]:
            layout=workspace_for(base,verified(subject));layout.database.parent.mkdir(parents=True)
            with sqlite3.connect(layout.database) as conn:
                conn.execute('create table marker(value text)');conn.execute('insert into marker values(?)',(installation+subject,))
            assert all(p.is_relative_to(layout.root) for p in [layout.database,layout.rag_database,layout.key,layout.pdfs,layout.backups,layout.cache,layout.mobile])
        # Fresh identity verification and fresh connections simulate storage reopen A→B→A.
        for subject in [A,B,A]:
            with sqlite3.connect(workspace_for(base,verified(subject)).database) as conn:
                assert conn.execute('select value from marker').fetchone()[0]==installation+subject
        assert legacy.read_bytes()==b'UNASSIGNED-LEGACY'
        assert not (base/'legacy-owner.json').exists()

def make_app(subject,token,expires):
    app=FastAPI()
    @app.get('/private')
    def private():return {'subject':subject}
    app.add_middleware(AccountBoundaryMiddleware,binding=AccountBinding(ISSUER,subject,hashlib.sha256(token.encode()).hexdigest(),expires))
    return app

def test_direct_cross_account_requests_rejected_even_with_fake_actor_or_dev_skip(monkeypatch):
    monkeypatch.setenv('DEV_SKIP_AUTH','1')
    a=TestClient(make_app(A,'synthetic-A',4102444800));b=TestClient(make_app(B,'synthetic-B',4102444800))
    for client,own,other,subject in [(a,'synthetic-A','synthetic-B',A),(b,'synthetic-B','synthetic-A',B)]:
        assert client.get('/private',headers={'Authorization':'Bearer '+other,'X-Actor-ID':subject}).status_code==401
        good=client.get('/private',headers={'Authorization':'Bearer '+own})
        assert good.json()['subject']==subject and good.headers['cache-control']=='no-store'
        assert client.get('/private').status_code==401

def test_delayed_response_is_discarded_after_session_expiry(monkeypatch):
    import account_boundary
    clock=[1];monkeypatch.setattr(account_boundary.time,'time',lambda:clock[0])
    app=make_app(A,'synthetic-A',100)
    @app.get('/delayed')
    def delayed():clock[0]=101;return {'private':'OLD-ACCOUNT-RESPONSE'}
    response=TestClient(app).get('/delayed',headers={'Authorization':'Bearer synthetic-A'})
    assert response.status_code==401 and 'OLD-ACCOUNT-RESPONSE' not in response.text

def test_missing_or_email_only_binding_is_denied():
    assert AccountBinding.from_environment({}) is None
    assert AccountBinding.from_environment({'MOADOOM_ACCOUNT_SUBJECT':'owner@example.invalid'}) is None
