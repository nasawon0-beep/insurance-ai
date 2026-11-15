"""Real engine repro; synthetic data only, no auth-bypass security claim."""

def test_audit_actor_is_server_bound_not_a_forged_header():
    import hashlib, time
    from account_boundary import AccountBinding, AccountBoundaryMiddleware
    from starlette.responses import JSONResponse
    subject='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    from starlette.applications import Starlette
    from starlette.routing import Route
    async def endpoint(request):
        return JSONResponse({'actor':request.headers.get('x-actor-id','')})
    app=Starlette(routes=[Route('/customers',endpoint)])
    app.add_middleware(AccountBoundaryMiddleware,binding=AccountBinding('https://fixture.supabase.co/auth/v1',subject,hashlib.sha256(b'fixture-A').hexdigest(),int(time.time())+60))
    client=TestClient(app)
    response=client.get('/customers',headers={'Authorization':'Bearer fixture-A','X-Actor-ID':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'})
    assert response.status_code==200
    assert response.json()['actor']==subject

import base64
from fastapi.testclient import TestClient
from database import db, repo, crypto


def test_shared_engine_secret_must_not_authorize_another_central_account(tmp_path, monkeypatch):
    monkeypatch.setenv('DEV_SKIP_AUTH','0')
    monkeypatch.setenv('CUSTOMER_DB_PATH',str(tmp_path/'customers.sqlite3'))
    monkeypatch.setenv('RAG_DB_PATH',str(tmp_path/'rag.sqlite3'))
    monkeypatch.setenv('CUSTOMER_DB_KEY_B64',base64.b64encode(b'a'*32).decode())
    monkeypatch.setenv('INSURANCE_AI_API_SECRET_FILE',str(tmp_path/'engine.secret'))
    crypto.reset_cache()
    c=db.connect();db.init_schema(c);repo.create_customer(c,{'name':'ACCOUNT-A-SYNTHETIC-PRIVATE'});c.close()
    import main
    # Do not start lifespan (no warmup, backup, models, auth database or listener).
    client=TestClient(main.app)
    client.headers.pop('X-Session-ID',None)
    response=client.get('/customers',headers={'Authorization':'Bearer account-b-unverified-token','X-Actor-ID':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'})
    assert response.status_code in (401,403),response.text
    assert 'ACCOUNT-A-SYNTHETIC-PRIVATE' not in response.text
    crypto.reset_cache()

