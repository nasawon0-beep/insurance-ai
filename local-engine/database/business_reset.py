"""Explicit PC-business reset. No file-tree deletion, auth changes or phone deletions.

Reset deliberately leaves mobile business sync on durable HOLD. Explicit recovery
restores the matched pre-reset business/sync snapshot, never just the HOLD flag.
Current account/device credentials are not rolled back.
"""
from __future__ import annotations
import base64
import json
import os
import re
import hashlib
import hmac
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from .crypto import get_cipher

HOLD_KEY='business_reset_sync_hold'
RESULT_KEY='business_reset_last_result'
BUSINESS_TABLES=(
    'sync_tombstone_acks','sync_operations','sync_conflicts','sync_changes','sync_entities',
    'coverage_analysis','coverage_analysis_runs','coverage_source_occurrences','policy_coverages',
    'family_relationship_batch_idempotency','family_relationships','consultations','policies',
    'import_batch_rrn','customers',
)
PRESERVED_TABLES=frozenset({
    'app_settings','usage_log','audit_log','rrn_access_log','coverage_catalog','coverage_adjustment_factors',
    'sync_devices','sync_device_list_hidden','sync_pairing_codes','sync_recovery_codes','sync_pairing_rate_limits',
})

def sync_is_held(conn) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone():
        return False
    row=conn.execute('SELECT value FROM app_settings WHERE key=?',(HOLD_KEY,)).fetchone()
    return bool(row and row[0])

def reset_business_data(conn, *, confirmation: str, confirmed: bool, request_id: str,
                        backup_directory: Path, fault_hook=None) -> dict:
    if confirmation != '초기화' or confirmed is not True:
        raise ValueError('정확한 초기화 입력과 최종 확인이 필요합니다.')
    if not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('잘못된 초기화 요청입니다.')
    if conn.in_transaction:
        raise RuntimeError('진행 중인 DB 작업이 있어 초기화할 수 없습니다.')
    conn.execute('BEGIN IMMEDIATE')
    try:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        unknown=tables-set(BUSINESS_TABLES)-PRESERVED_TABLES
        if unknown or not {'customers','app_settings'} <= tables:
            raise RuntimeError('데이터 구조를 확인할 수 없어 초기화를 중단했습니다.')
        if request_id in json.loads(_setting(conn, 'business_reset_restored_requests') or '[]'):
            raise ValueError('이미 복원한 초기화 요청은 다시 실행할 수 없습니다.')
        previous=conn.execute('SELECT value FROM app_settings WHERE key=?',(RESULT_KEY,)).fetchone()
        if previous:
            result=json.loads(previous[0])
            if result['request_id']==request_id:
                conn.rollback();return {**result,'replayed':True}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():
            raise RuntimeError('추가 DB 처리 규칙이 있어 안전한 초기화 검수가 필요합니다.')
        counts={t:conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in BUSINESS_TABLES if t in tables}
        # Serialize a transaction-consistent snapshot in memory. Never write a plaintext PII copy.
        if conn.execute('PRAGMA page_count').fetchone()[0]*conn.execute('PRAGMA page_size').fetchone()[0]>128*1024*1024:
            raise RuntimeError('DB가 커서 안전 백업을 별도로 확인해야 합니다. 자료는 삭제하지 않았습니다.')
        encrypted=get_cipher().encrypt(base64.b64encode(conn.serialize()).decode('ascii'))
        if not encrypted or not encrypted.startswith('enc:v1:'):
            raise RuntimeError('암호화된 안전 백업을 만들 수 없습니다.')
        backup_directory=Path(backup_directory)
        backup_directory.mkdir(parents=True,exist_ok=True)
        target=backup_directory/f'business-before-reset-{request_id}-{os.urandom(8).hex()}.enc'
        created=False
        try:
            with target.open('x',encoding='utf-8') as output:
                created=True
                output.write(encrypted);output.flush();os.fsync(output.fileno())
        except Exception:
            if created:target.unlink(missing_ok=True)
            raise
        now=datetime.now(timezone.utc).isoformat()
        # Hold and data removal commit together: refresh can never generate reset tombstones.
        conn.execute('INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)',(HOLD_KEY,request_id,now))
        for table in BUSINESS_TABLES:
            if table in tables:
                conn.execute(f'DELETE FROM "{table}"')
                if fault_hook: fault_hook(table)
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('관련 자료 검증 실패로 초기화를 취소했습니다.')
        result={'request_id':request_id,'deleted':counts,'sync_paused':True,'replayed':False,'backup_filename':target.name,
                'backup_sha256':hashlib.sha256(encrypted.encode('utf-8')).hexdigest()}
        conn.execute('INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)',(RESULT_KEY,json.dumps(result,ensure_ascii=False),now))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


RESTORE_KEY = 'business_reset_last_restore'
MAX_SNAPSHOT = 128 * 1024 * 1024


def _setting(conn, key):
    row = conn.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def _schema(conn):
    # Compare the complete schema, not user_version alone (this project uses migrations).
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
        raise ValueError('추가 DB 처리 규칙은 복원할 수 없습니다.')
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables - set(BUSINESS_TABLES) - PRESERVED_TABLES or not {'customers','app_settings'} <= tables:
        raise ValueError('지원하지 않는 백업 구조입니다.')
    return sorted(tuple(r) for r in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))


def _encrypted_safety_copy(conn, backup_directory, request_id):
    if conn.execute('PRAGMA page_count').fetchone()[0] * conn.execute('PRAGMA page_size').fetchone()[0] > MAX_SNAPSHOT:
        raise ValueError('안전 사본 크기를 확인해야 합니다.')
    encrypted = get_cipher().encrypt(base64.b64encode(conn.serialize()).decode('ascii'))
    if not encrypted or not encrypted.startswith('enc:v1:'):
        raise ValueError('암호화 안전 사본을 만들 수 없습니다.')
    target = Path(backup_directory) / f'business-before-restore-{request_id}-{os.urandom(8).hex()}.enc'
    created = False
    try:
        with target.open('x', encoding='utf-8') as output:
            created = True
            output.write(encrypted); output.flush(); os.fsync(output.fileno())
    except Exception:
        if created: target.unlink(missing_ok=True)
        raise
    return target.name


def restore_business_data(conn, *, confirmation, confirmed, request_id, backup_filename,
                          backup_directory, fault_hook=None):
    """Restore only the authenticated snapshot belonging to this DB's current reset.

    Accounts/devices/settings stay current. Only the business/sync tables and the
    snapshot's two reset-control keys are restored in one transaction. A pre-held
    snapshot stays held and exposes its preceding recovery receipt (no naked unlock).
    """
    if confirmation != '복원' or confirmed is not True:
        raise ValueError('정확한 복원 입력과 최종 확인이 필요합니다.')
    if not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('잘못된 복원 요청입니다.')
    if not re.fullmatch(r'business-before-reset-[0-9a-f]{32}-[0-9a-f]{16}\.enc', backup_filename):
        raise ValueError('이 초기화의 안전 백업만 복원할 수 있습니다.')
    if conn.in_transaction:
        raise RuntimeError('진행 중인 DB 작업이 있습니다.')
    snapshot = None
    conn.execute('BEGIN IMMEDIATE')
    try:
        previous = _setting(conn, RESTORE_KEY)
        if previous:
            receipt = json.loads(previous)
            if receipt['request_id'] == request_id:
                if receipt['backup_filename'] != backup_filename:
                    raise ValueError('같은 요청의 백업을 변경할 수 없습니다.')
                conn.rollback()
                return {**receipt, 'replayed': True, 'sync_paused': sync_is_held(conn)}
        current = json.loads(_setting(conn, RESULT_KEY) or '{}')
        if not sync_is_held(conn) or current.get('backup_filename') != backup_filename:
            raise ValueError('현재 초기화에 연결된 안전 백업이 아닙니다.')
        if _setting(conn, HOLD_KEY) != current.get('request_id'):
            raise ValueError('초기화 상태를 검증하지 못했습니다.')
        directory = Path(backup_directory).resolve()
        target = directory / backup_filename
        if target.is_symlink() or target.resolve().parent != directory:
            raise ValueError('안전 백업 위치를 검증하지 못했습니다.')
        with target.open('rb') as source:
            ciphertext = source.read(MAX_SNAPSHOT * 2 + 1)
        if len(ciphertext) > MAX_SNAPSHOT * 2 or not hmac.compare_digest(
                hashlib.sha256(ciphertext).hexdigest(), current.get('backup_sha256', '')):
            raise ValueError('백업 검증 정보가 없거나 파일이 변경되었습니다.')
        text = ciphertext.decode('utf-8')
        if not text.startswith('enc:v1:'):
            raise ValueError('암호화 안전 백업이 아닙니다.')
        decrypted = get_cipher(create_if_missing=False).decrypt(text)
        if not isinstance(decrypted, str):
            raise ValueError('안전 백업의 키를 확인하지 못했습니다.')
        raw = base64.b64decode(decrypted, validate=True)
        if len(raw) > MAX_SNAPSHOT or raw[:16] != b'SQLite format 3\x00':
            raise ValueError('SQLite 안전 백업 형식이 아닙니다.')
        # Serialized WAL databases cannot open a WAL beside an in-memory image.
        # The serialized image already contains the committed pages; normalize only
        # journal-mode header bytes in memory, never any operating database/file.
        raw = raw[:18] + b'\x01\x01' + raw[20:]
        snapshot = sqlite3.connect(':memory:')
        snapshot.execute('PRAGMA trusted_schema=OFF')
        snapshot.deserialize(raw)
        snapshot.execute('PRAGMA query_only=ON')
        if snapshot.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('백업 무결성 검증에 실패했습니다.')
        if snapshot.execute('PRAGMA foreign_key_check').fetchone() or _schema(snapshot) != _schema(conn):
            raise ValueError('현재 DB와 백업의 스키마가 다릅니다.')
        safety = _encrypted_safety_copy(conn, directory, request_id)
        tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in BUSINESS_TABLES:
            if table in tables:
                conn.execute(f'DELETE FROM "{table}"')
                if fault_hook: fault_hook(table)
        for table in reversed(BUSINESS_TABLES):
            if table in tables:
                rows = snapshot.execute(f'SELECT * FROM "{table}"')
                placeholders = ','.join('?' for _ in rows.description)
                conn.executemany(f'INSERT INTO "{table}" VALUES({placeholders})', rows)
        now = datetime.now(timezone.utc).isoformat()
        for key in (HOLD_KEY, RESULT_KEY):
            value = _setting(snapshot, key)
            conn.execute('DELETE FROM app_settings WHERE key=?', (key,))
            if value is not None:
                conn.execute('INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)', (key,value,now))
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('현재 기기와 복원 자료의 관계를 검증하지 못했습니다.')
        restored_requests = json.loads(_setting(conn, 'business_reset_restored_requests') or '[]')
        if current['request_id'] not in restored_requests:
            restored_requests.append(current['request_id'])
        conn.execute('INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)',
                     ('business_reset_restored_requests',json.dumps(restored_requests),now))
        result = {'request_id': request_id, 'backup_filename': backup_filename,
                  'safety_copy': safety, 'restored': True, 'sync_paused': sync_is_held(conn), 'replayed': False}
        conn.execute('INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)',
                     (RESTORE_KEY,json.dumps(result,ensure_ascii=False),now))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        if snapshot is not None: snapshot.close()
