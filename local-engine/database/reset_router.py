"""Loopback-only engine router, retaining the engine secret and outer auth guards."""
import secrets
import json
import socket
from contextlib import contextmanager
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,StrictBool
from .db import connect,resolve_db_path
from .business_reset import reset_business_data,sync_is_held,restore_business_data,RESULT_KEY,RESTORE_KEY
from .reset_activity import REQUESTS

def authorized(request:Request):
    secret=getattr(request.app.state,'api_secret',None)
    header=getattr(request.app.state,'api_secret_header','X-Insurance-AI-Secret')
    supplied=request.headers.get(header,'')
    if not isinstance(secret,str) or not secret or not secrets.compare_digest(supplied.encode(),secret.encode()):
        raise HTTPException(status_code=401,detail='인증이 필요합니다.')

router=APIRouter(prefix='/business/reset',dependencies=[Depends(authorized)])

@contextmanager
def mobile_port_guard():
    # No listener is opened. Reserve IPv4 TCP18443 exclusively while resetting so
    # an old sidecar cannot publish stale snapshots or restart across the commit.
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as reservation:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            reservation.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        reservation.bind(('0.0.0.0',18443))
        yield

class ResetInput(BaseModel):
    confirmation:str
    confirmed:StrictBool
    request_id:str

class RestoreInput(ResetInput):
    backup_filename:str

@router.get('/status')
def status():
    conn=connect()
    try:
        held=sync_is_held(conn)
        row=conn.execute('SELECT value FROM app_settings WHERE key=?',(RESULT_KEY,)).fetchone()
        receipt=json.loads(row[0]) if row else {}
        candidate=receipt.get('backup_filename') if held and receipt.get('backup_sha256') else None
        restored=conn.execute('SELECT value FROM app_settings WHERE key=?',(RESTORE_KEY,)).fetchone()
        safety=json.loads(restored[0]).get('safety_copy') if restored else None
        return {'sync_paused':held,'restore_candidate':candidate,'last_restore_safety_copy':safety}
    finally:conn.close()

@router.post('/restore')
def restore(body: 'RestoreInput'):
    if not REQUESTS.resetting:
        raise HTTPException(status_code=409,detail='안전한 작업 중지 상태를 확인하지 못했습니다.')
    if body.confirmation!='복원' or body.confirmed is not True:
        raise HTTPException(status_code=422,detail='정확한 복원 입력과 최종 확인이 필요합니다.')
    conn=connect()
    try:
        with mobile_port_guard():
            return restore_business_data(conn,confirmation=body.confirmation,confirmed=body.confirmed,
                request_id=body.request_id,backup_filename=body.backup_filename,
                backup_directory=resolve_db_path().parent/'backups')
    except ValueError as error:
        raise HTTPException(status_code=422,detail='백업·키·스키마·초기화 상태를 검증하지 못했습니다. 현재 자료는 유지됩니다.') from error
    except Exception as error:
        raise HTTPException(status_code=409,detail='복원하지 못했습니다. 백업·암호화 키·휴대폰 연결·작업 상태를 확인해 주세요. 현재 자료는 유지됩니다.') from error
    finally:conn.close()

@router.post('')
def reset(body:ResetInput):
    if not REQUESTS.resetting:
        raise HTTPException(status_code=409,detail='안전한 작업 중지 상태를 확인하지 못했습니다.')
    if body.confirmation!='초기화' or body.confirmed is not True:
        raise HTTPException(status_code=422,detail='정확한 초기화 입력과 최종 확인이 필요합니다.')
    conn=connect()
    try:
        with mobile_port_guard():
            return reset_business_data(conn,confirmation=body.confirmation,confirmed=body.confirmed,
                request_id=body.request_id,backup_directory=resolve_db_path().parent/'backups')
    except ValueError as error:
        raise HTTPException(status_code=422,detail=str(error)) from error
    except Exception as error:
        # Do not leak paths, PII, crypto values or make a successful deletion claim.
        raise HTTPException(status_code=409,detail='초기화를 완료하지 못했습니다. 작업·DB 구조·암호화 백업 상태를 확인해 주세요. 삭제 작업은 취소되었습니다.') from error
    finally:conn.close()
