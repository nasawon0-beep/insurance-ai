"""Serialize destructive reset against complete HTTP/background response lifetimes."""
from contextlib import contextmanager
from threading import Lock
from starlette.responses import JSONResponse

class ResetBusy(RuntimeError):
    pass

class ActivityGate:
    def __init__(self):
        self.lock=Lock();self.active=0;self.resetting=False
    @contextmanager
    def work(self):
        with self.lock:
            if self.resetting:raise ResetBusy('초기화 중입니다. 잠시 후 다시 시도해 주세요.')
            self.active+=1
        try:yield
        finally:
            with self.lock:self.active-=1
    @contextmanager
    def reset(self):
        with self.lock:
            if self.resetting or self.active:raise ResetBusy('진행 중인 작업이 있습니다. 작업이 끝난 뒤 다시 시도해 주세요.')
            self.resetting=True
        try:yield
        finally:
            with self.lock:self.resetting=False

REQUESTS=ActivityGate()

class BusinessResetActivityMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        is_reset=scope.get('method')=='POST' and scope.get('path') in {'/business/reset','/business/reset/restore'}
        guard=REQUESTS.reset() if is_reset else REQUESTS.work()
        try:
            with guard:
                # Await the whole ASGI application, including streamed body/background tasks.
                await self.app(scope,receive,send)
        except ResetBusy as error:
            await JSONResponse(status_code=409,content={'detail':str(error)})(scope,receive,send)
