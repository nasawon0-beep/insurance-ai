"""Explicit synthetic trusted-launcher binding, never an authentication bypass.

Use only from positive fixtures; negative cases must omit/replace their bearer.
The real AccountBoundaryMiddleware reads these inputs and validates each request.
"""
import hashlib

SUBJECT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TOKEN = 'synthetic-explicit-account-fixture'
AUTHORIZATION = 'Bearer ' + TOKEN
FIELDS = ('MOADOOM_ACCOUNT_ISSUER', 'MOADOOM_ACCOUNT_SUBJECT', 'MOADOOM_ACCOUNT_TOKEN_SHA256', 'MOADOOM_ACCOUNT_EXPIRES_AT')


def bind_synthetic_account(monkeypatch, app, *, expires_at=4102444800):
    values = ('https://fixture.supabase.co/auth/v1', SUBJECT,
              hashlib.sha256(TOKEN.encode()).hexdigest(), str(expires_at))
    for name, value in zip(FIELDS, values):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('DEV_SKIP_AUTH', '0')
    # Starlette freezes launcher binding when it first builds its stack.
    # Restore the previous stack at fixture teardown, not the product guard.
    monkeypatch.setattr(app, 'middleware_stack', None)
