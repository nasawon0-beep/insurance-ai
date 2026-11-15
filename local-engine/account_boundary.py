"""Server-side, immutable account-bound engine guard.

A launcher must verify central authentication and launch a fresh process for a
single account. Absence of a binding denies business requests, never falls back
to the legacy shared database. Environment is a trusted launcher boundary, not
an HTTP/UI-supplied principal. This does not defend against OS file administrators.
"""
from dataclasses import dataclass, field
from authority_clock import AuthorityClock
import hashlib
import hmac
import os
import re
import time
from uuid import UUID
from starlette.responses import JSONResponse

@dataclass(frozen=True)
class AccountBinding:
    issuer: str
    subject: str
    token_sha256: str
    expires_at: int
    clock: AuthorityClock = field(default_factory=AuthorityClock,repr=False,compare=False)

    @classmethod
    def from_environment(cls, env=None):
        env = os.environ if env is None else env
        try:
            issuer=env['MOADOOM_ACCOUNT_ISSUER']
            subject=env['MOADOOM_ACCOUNT_SUBJECT']
            digest=env['MOADOOM_ACCOUNT_TOKEN_SHA256']
            expiry=int(env['MOADOOM_ACCOUNT_EXPIRES_AT'])
            if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co/auth/v1',issuer): return None
            if str(UUID(subject))!=subject or not re.fullmatch(r'[a-f0-9]{64}',digest): return None
            return cls(issuer,subject,digest,expiry)
        except (KeyError,ValueError,TypeError): return None

    def accepts(self, authorization, now=None):
        if now is None and not self.clock.valid_until(self.expires_at): return False
        now=time.time() if now is None else now
        if now>=self.expires_at or not authorization.startswith('Bearer '): return False
        token=authorization[7:]
        return 0<len(token)<=16384 and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),self.token_sha256)

class AccountBoundaryMiddleware:
    """Reject unbound/cross-account direct API calls even if API secret is known.

    No DEV_SKIP_AUTH or QA bypass. Health/bootstrap return no business data.
    Checking again before sending prevents expired credentials leaking a delayed
    response. Changing identity requires process replacement, not mutation here.
    """
    def __init__(self,app,binding=None):
        self.app=app
        self.binding=AccountBinding.from_environment() if binding is None else binding

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        if scope.get('method')=='OPTIONS' or scope.get('path') in {'/health','/api-secret'}:
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        authorization=headers.get(b'authorization',b'').decode('latin-1')
        if not self.binding or not self.binding.accepts(authorization):
            return await JSONResponse(status_code=401,content={'detail':'verified_account_workspace_required'})(scope,receive,send)
        scope.setdefault('state',{})['account_subject']=self.binding.subject
        # Existing audit consumers read this header. Never trust the caller's actor.
        scope['headers']=[(k,v) for k,v in scope.get('headers',[]) if k.lower()!=b'x-actor-id']+[(b'x-actor-id',self.binding.subject.encode('ascii'))]
        sent=False
        blocked=False
        async def fenced_send(message):
            nonlocal sent,blocked
            if blocked:return
            if not self.binding.accepts(authorization):
                blocked=True
                if not sent:
                    await JSONResponse(status_code=401,content={'detail':'account_session_expired'})(scope,receive,send)
                else:
                    await send({'type':'http.response.body','body':b'','more_body':False})
                return
            if message['type']=='http.response.start':
                sent=True
                message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'cache-control']+[(b'cache-control',b'no-store')]}
            await send(message)
        return await self.app(scope,receive,fenced_send)
