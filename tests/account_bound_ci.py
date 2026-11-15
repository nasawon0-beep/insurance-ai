"""Opt-in CI trusted-launcher fixture for legacy business API tests, NOT real Auth.
Load explicitly with PYTHONPATH=tests python -m pytest -p account_bound_ci.
Do not load for authentication/boundary suites: those own their identities/tokens.
"""
import hashlib,os
import pytest
TOKEN='synthetic-account-bound-ci'
SUBJECT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'

def pytest_configure(config):
    import tempfile
    from pathlib import Path
    parent=Path(os.environ['MOADOOM_CI_FIXTURE_ROOT']) if os.environ.get('MOADOOM_CI_FIXTURE_ROOT') else Path(config.rootpath)/'.build-tmp'/'ci-account-bound'
    parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='fixture-',dir=parent))
    # Establish isolated storage before business modules are imported at collection.
    os.environ.update({'CUSTOMER_DB_PATH':str(root/'customers.sqlite3'),'CUSTOMER_DB_KEYFILE':str(root/'customer-db.key'),'CUSTOMER_DB_KEYFILE_ONLY':'1','CUSTOMER_DB_KEY_B64':'','RAG_DB_PATH':str(root/'rag.sqlite3'),'INSURANCE_AI_API_SECRET_FILE':str(root/'api.secret'),'COVERAGE_ANALYSIS_LOG_DIR':str(root/'logs'),'APPDATA':str(root/'appdata'),'LOCALAPPDATA':str(root/'localappdata')})
    os.environ.update({'MOADOOM_ACCOUNT_ISSUER':'https://fixture.supabase.co/auth/v1','MOADOOM_ACCOUNT_SUBJECT':SUBJECT,'MOADOOM_ACCOUNT_TOKEN_SHA256':hashlib.sha256(TOKEN.encode()).hexdigest(),'MOADOOM_ACCOUNT_EXPIRES_AT':'4102444800'})

@pytest.fixture(autouse=True)
def account_bound_client(monkeypatch,auto_login_session):
    monkeypatch.setenv('DEV_SKIP_AUTH','0')
    from starlette.testclient import TestClient
    previous=TestClient.__init__
    def init(self,*args,**kwargs):
        previous(self,*args,**kwargs)
        if getattr(self.app.state,'api_secret',None):
            self.headers.setdefault('Authorization','Bearer '+TOKEN)
    monkeypatch.setattr(TestClient,'__init__',init)
