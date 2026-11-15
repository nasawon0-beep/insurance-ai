"""Explicit, offline maintenance migration; never invoked on first login.

Caller must obtain VerifiedPrincipal by online verification and a separately
reviewed OwnerApproval for this exact plan. These Python objects are NOT signed
authority and must never be accepted from an HTTP/UI request. No API or automatic
production invocation is exposed here. Source files are never changed/deleted.
Backup keys are supplied by the trusted operator, never stored beside backups.
"""
from contextlib import contextmanager, ExitStack, closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import base64
import hashlib
import json
import os
import sqlite3
import uuid
from cryptography.fernet import Fernet, InvalidToken
from account_workspace import VerifiedPrincipal, workspace_for

class MigrationError(RuntimeError):
    pass

@dataclass(frozen=True)
class OwnerApproval:
    issuer: str
    subject: str
    binding: str

def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _safe(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.stat(follow_symlinks=False),'st_file_attributes',0)&0x400):
            raise MigrationError('Reparse point refused')
    if path.is_file() and path.stat().st_nlink>1:
        raise MigrationError('Hard-linked file refused')
    return path

def _relative(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise MigrationError('Unsafe relative path')
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p)!=name:
        raise MigrationError('Unsafe relative path')
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if any(part.rstrip(' .')!=part or part.split('.')[0].upper() in reserved for part in p.parts):
        raise MigrationError('Windows path alias refused')
    return name

def plan_migration(legacy_root, installation_root, files):
    source=_safe(legacy_root); installation=_safe(installation_root)
    if not source.is_dir() or not installation.is_dir() or not files:
        raise MigrationError('Existing roots and explicit file manifest required')
    records={}
    if len({name.casefold() for name in files})!=len(files):
        raise MigrationError('Case-insensitive destination collision')
    for destination,relative in sorted(files.items()):
        _relative(destination); _relative(relative)
        if PurePosixPath(destination).parts[0] not in {'data','config','pdfs','cache','logs'}:
            raise MigrationError('Unsupported destination')
        if 'mobile' in destination.lower() or 'secret' in destination.lower():
            raise MigrationError('Process secrets/mobile credentials must not migrate')
        path=_safe(source/relative)
        if not path.is_file():raise MigrationError('Source file missing')
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):
            raise MigrationError('Close/checkpoint source SQLite before migration')
        records[destination]={'source':relative,'sha256':_sha(path.read_bytes())}
    value={'source':str(source),'installation':str(installation),'files':records}
    value['binding']=_sha(_json(value))
    return value

def _tree_snapshot(root):
    root=_safe(root)
    if not root.is_dir():raise MigrationError('Existing workspace required')
    files={};dirs=[]
    for directory,children,names in os.walk(root,followlinks=False):
        for name in children+names:_safe(Path(directory)/name)
        dirs.extend((Path(directory)/name).relative_to(root).as_posix() for name in children)
        for name in names:
            path=Path(directory)/name
            if not path.is_file():raise MigrationError('Non-regular workspace entry')
            files[path.relative_to(root).as_posix()]=_sha(path.read_bytes())
    return {'files':files,'dirs':sorted(dirs)}

def _assert_snapshot(root,snapshot):
    if _tree_snapshot(root)!=snapshot:raise MigrationError('Workspace changed; preserve both sides and obtain new review')

def _empty_sqlite(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()!=('ok',):raise MigrationError('Invalid workspace database')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():raise MigrationError('Non-bootstrap database schema')
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if conn.execute('SELECT 1 FROM "'+table.replace('"','""')+'" LIMIT 1').fetchone():
                raise MigrationError('Workspace/backup contains records; preserve both sides, no automatic merge')

def _bound_backups(root,snapshot,principal,*,cipher=None,empty=False):
    # Same authenticated manifest format as database.backup._verify_snapshot.
    # No unbound legacy backup is assigned to an owner by this classifier.
    import re
    names={name for name in snapshot['files'] if name.startswith('data/backups/')}
    if not names:return set()
    if cipher is None:
        from protected_business_key import decode_key
        from database.crypto import FieldCipher
        cipher=FieldCipher(decode_key((root/'config/customer-db.key').read_bytes(),principal.issuer,principal.subject),'reviewed-bootstrap')
    checked=set()
    for name in sorted(names):
        base=name.removesuffix('.owner.enc')
        if not re.fullmatch(r'data/backups/customers-\d{8}-\d{6}(?:_\d+)?\.sqlite3',base):raise MigrationError('Unknown backup requires separate review')
        marker=base+'.owner.enc'
        if base not in names or marker not in names:raise MigrationError('Unbound backup requires separate review')
        if base in checked:continue
        encrypted=(root/marker).read_text(encoding='utf-8')
        if len(encrypted)>8192 or not encrypted.startswith('enc:v1:'):raise MigrationError('Unprotected backup owner manifest')
        plain=cipher.decrypt(encrypted)
        if not isinstance(plain,str):raise MigrationError('Invalid backup owner manifest')
        manifest=json.loads(plain)
        if manifest!={'v':1,'purpose':'account-database-backup','owner':[principal.issuer,principal.subject],'sha256':snapshot['files'][base]}:
            raise MigrationError('Backup owner/key/digest mismatch')
        if empty:_empty_sqlite(root/base)
        checked.update({base,marker})
    return checked

def _empty_bootstrap(root,principal):
    snapshot=_tree_snapshot(root)
    if any(name.endswith(('-wal','-shm','-journal')) for name in snapshot['files']):
        raise MigrationError('Close/checkpoint workspace SQLite before review')
    for name in snapshot['dirs']:
        if name.split('/')[0] not in {'data','config','pdfs','cache','logs'}:
            raise MigrationError('Unknown workspace directory; manual review required')
    backups=_bound_backups(root,snapshot,principal,empty=True)
    for name in snapshot['files']:
        path=root/name
        if name in backups:continue
        if name in {'data/customers.sqlite3','data/rag-index.sqlite3'}:
            # No read-only WAL open: even it can create SHM on Windows.
            _empty_sqlite(path)
        elif name=='config/customer-db.key':
            from protected_business_key import decode_key
            decode_key(path.read_bytes(),principal.issuer,principal.subject)
        elif name in {'data/local-engine-api.secret','data/runtime/local-engine-api.secret'}:
            import re
            if not re.fullmatch(b'[0-9a-f]{64}',path.read_bytes()):raise MigrationError('Unknown runtime secret format')
        else:
            raise MigrationError('Workspace contains files/attachments/credentials; preserve both sides, no automatic merge')
    _assert_snapshot(root,snapshot)
    return snapshot

def _format_upgrade(root,principal,files):
    snapshot=_tree_snapshot(root)
    runtime={'data/local-engine-api.secret','data/runtime/local-engine-api.secret'}
    if any(dest!=entry['source'] for dest,entry in files.items()):raise MigrationError('Format upgrade cannot remap files')
    if set(files)!=set(snapshot['files'])-runtime:raise MigrationError('Complete same-owner workspace manifest required; no silent omissions')
    for name in files:
        if name not in {'config/customer-db.key','data/customers.sqlite3','data/rag-index.sqlite3'} and not name.startswith(('pdfs/','data/backups/')):
            raise MigrationError('Unknown workspace content/credentials; separate policy review required')
    from protected_business_key import decode_key
    from database.crypto import FieldCipher
    raw=(root/'config/customer-db.key').read_bytes();protected=raw.lstrip().startswith(b'{')
    key=decode_key(raw,principal.issuer,principal.subject) if protected else base64.b64decode(raw.strip(),validate=True)
    cipher=FieldCipher(key,'reviewed-same-owner');verified=0
    backups=_bound_backups(root,snapshot,principal,cipher=cipher)
    database=root/'data/customers.sqlite3'
    if database.exists():
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()!=('ok',):raise MigrationError('Invalid original database')
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                for row in conn.execute('SELECT * FROM "'+table.replace('"','""')+'"'):
                    for value in row:
                        if isinstance(value,str) and value.startswith('enc:v1:'):
                            cipher.decrypt(value);verified+=1
    if not protected and not verified and not backups:raise MigrationError('Legacy key cannot be verified against existing ciphertext; manual recovery review required')
    _assert_snapshot(root,snapshot)
    return snapshot

def _eligible_target(root,principal,plan):
    return _empty_bootstrap(root,principal) if plan['replacement']['mode']=='empty-bootstrap' else _format_upgrade(root,principal,plan['files'])

def plan_workspace_transition(source,installation,files,principal,*,mode):
    if mode not in {'empty-bootstrap','same-owner-format'}:raise MigrationError('Unsupported workspace transition')
    target=workspace_for(installation,principal).root
    if (_safe(source)==target)!=(mode=='same-owner-format'):raise MigrationError('Reviewed source/target mode mismatch')
    plan=plan_migration(source,installation,files)
    snapshot=_empty_bootstrap(target,principal) if mode=='empty-bootstrap' else _format_upgrade(target,principal,plan['files'])
    plan['replacement']={'mode':mode,'issuer':principal.issuer,'subject':principal.subject,'snapshot':snapshot}
    plan['binding']=_sha(_json({k:v for k,v in plan.items() if k!='binding'}))
    return plan

def _replacement(plan,principal):
    value=plan.get('replacement')
    if value is not None:
        if set(value)!={'mode','issuer','subject','snapshot'} or value['mode'] not in {'empty-bootstrap','same-owner-format'} or (value['issuer'],value['subject'])!=(principal.issuer,principal.subject):
            raise MigrationError('Reviewed replacement owner/mode mismatch')
    return value

def _transition_marker(target):
    return target.parent/('.transition-'+target.name)

def _clear_transition_marker(target,binding):
    marker=_safe(_transition_marker(target))
    if marker.exists():
        if marker.read_bytes()!=binding.encode('ascii'):raise MigrationError('Other workspace transition pending')
        marker.unlink()

@contextmanager
def _lease(path):
    path=_safe(path); path.parent.mkdir(parents=True,exist_ok=True)
    with open(path,'a+b') as handle:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:raise MigrationError('Application/migration is running') from error
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)

def _write(path,data,temp_root=None):
    path=_safe(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp_root=_safe(temp_root or path.parent);temp_root.mkdir(parents=True,exist_ok=True)
    tmp=temp_root/(uuid.uuid4().hex+'.tmp')
    with open(tmp,'xb') as handle:
        handle.write(data);handle.flush();os.fsync(handle.fileno())
    os.replace(tmp,path)

def _read(path,cipher):
    try:return json.loads(cipher.decrypt(_safe(path).read_bytes()))
    except (InvalidToken,ValueError,OSError) as error:raise MigrationError('Encrypted artifact validation failed') from error

def _validate(plan,principal,approval,source_required=True):
    if not isinstance(principal,VerifiedPrincipal) or not isinstance(approval,OwnerApproval):
        raise MigrationError('Verified identity AND explicit operator ownership approval required')
    if (approval.issuer,approval.subject,approval.binding)!=(principal.issuer,principal.subject,plan['binding']):
        raise MigrationError('Owner or installation/source binding mismatch')
    if _sha(_json({k:v for k,v in plan.items() if k!='binding'}))!=plan['binding']:
        raise MigrationError('Plan binding changed')
    replacement=_replacement(plan,principal)
    source=Path(plan['source'])
    if replacement:
        target=workspace_for(plan['installation'],principal).root
        if (_safe(source)==target)!=(replacement['mode']=='same-owner-format'):raise MigrationError('Reviewed source/target mode mismatch')
        previous=_safe(Path(plan['installation'])/'.account-migrations'/plan['binding']/'previous-workspace')
        if replacement['mode']=='same-owner-format' and previous.exists():
            _assert_snapshot(previous,replacement['snapshot']);source=previous
    if source_required:
        fresh=plan_migration(source,plan['installation'],{k:v['source'] for k,v in plan['files'].items()})
        if replacement:
            fresh['source']=plan['source']
            fresh['replacement']=replacement
            fresh['binding']=_sha(_json({k:v for k,v in fresh.items() if k!='binding'}))
        if fresh!=plan:raise MigrationError('Source/plan changed; new review required')
    return workspace_for(plan['installation'],principal).root

@contextmanager
def _transaction(plan,principal,approval,key,source_required=True):
    target=_validate(plan,principal,approval,source_required)
    cipher=Fernet(key)
    root=Path(plan['installation']);job=_safe(root/'.account-migrations'/plan['binding'])
    with ExitStack() as stack:
        stack.enter_context(_lease(root/'app.lock'))
        stack.enter_context(_lease(root/'runtime'/'app.lock'))
        stack.enter_context(_lease(job/'operation.lock'))
        _validate(plan,principal,approval,source_required)
        yield target,job,cipher

def _check_tree(root,expected,expected_dirs=None):
    _safe(root)
    actual={};actual_dirs=[]
    for directory,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:_safe(Path(directory)/name)
        actual_dirs.extend((Path(directory)/name).relative_to(root).as_posix() for name in dirs)
        for name in files:
            path=Path(directory)/name
            actual[str(path.relative_to(root)).replace('\\','/')]=_sha(path.read_bytes())
    if actual!=expected or (expected_dirs is not None and sorted(actual_dirs)!=expected_dirs):raise MigrationError('Staging/promoted content changed; refuse overwrite')

def migrate(plan,principal,approval,key,*,checkpoint=lambda phase:None):
    with _transaction(plan,principal,approval,key) as (target,job,cipher):
        journal=job/'journal.enc';backup=job/'backup.enc';stage=job/'staging'
        replacement=_replacement(plan,principal);previous=job/'previous-workspace'
        identity={'binding':plan['binding'],'issuer':principal.issuer,'subject':principal.subject}
        if journal.exists():
            state=_read(journal,cipher)
            if state['identity']!=identity:raise MigrationError('Journal owner mismatch')
        else:
            if stage.exists() or previous.exists():raise MigrationError('Existing workspace/staging cannot be adopted')
            if replacement:
                _assert_snapshot(target,replacement['snapshot'])
                if _eligible_target(target,principal,plan)!=replacement['snapshot']:raise MigrationError('Workspace changed')
            elif target.exists():raise MigrationError('Existing workspace/staging cannot be adopted')
            payload={'identity':identity,'files':{name:base64.b64encode(_safe(Path(plan['source'])/entry['source']).read_bytes()).decode() for name,entry in plan['files'].items()}}
            if replacement:
                payload['previous']={name:base64.b64encode((target/name).read_bytes()).decode() for name in replacement['snapshot']['files']}
                _assert_snapshot(target,replacement['snapshot'])
            if backup.exists():
                if _read(backup,cipher)!=payload:raise MigrationError('Backup mismatch')
            else:_write(backup,cipher.encrypt(_json(payload)))
            if _read(backup,cipher)!=payload:raise MigrationError('Backup readback failed')
            state={'identity':identity,'phase':'backup','backup':str(backup)}
            _write(journal,cipher.encrypt(_json(state)))
            checkpoint('backup')
        payload=_read(backup,cipher)
        if payload['identity']!=identity:raise MigrationError('Backup owner mismatch')
        if state['phase'] in {'rolled_back','rolling_back'}:raise MigrationError('Rolled back; explicit new review required')
        if state['phase']=='backup':
            if replacement:
                _assert_snapshot(target,replacement['snapshot'])
                if _eligible_target(target,principal,plan)!=replacement['snapshot']:raise MigrationError('Workspace changed')
            elif target.exists():raise MigrationError('Destination appeared before staging')
            _safe(stage).mkdir(exist_ok=True)
            for name,encoded in payload['files'].items():
                data=base64.b64decode(encoded,validate=True)
                if _sha(data)!=plan['files'][name]['sha256']:raise MigrationError('Snapshot hash mismatch')
                path=stage/_relative(name);_write(path,data,job/'pending-writes')
                if path.suffix=='.sqlite3':
                    with closing(sqlite3.connect(path)) as conn:
                        if conn.execute('PRAGMA quick_check').fetchone()!=('ok',):raise MigrationError('Invalid SQLite snapshot')
                        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'sync_*'")]
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name GLOB 'sync_*'").fetchone():raise MigrationError('Sync triggers require manual review')
                        if not replacement or replacement['mode']!='same-owner-format':
                            for table in tables:conn.execute('DELETE FROM "'+table.replace('"','""')+'"')
                        conn.commit()
            # Only the already approved, locked migration converts storage formats.
            # Source and encrypted original backup remain byte-for-byte recoverable.
            key_path=stage/'config/customer-db.key'
            rag_path=stage/'data/rag-index.sqlite3'
            if key_path.exists():
                from protected_business_key import decode_key,encode_key
                raw=key_path.read_bytes()
                business_key=decode_key(raw,principal.issuer,principal.subject) if raw.lstrip().startswith(b'{') else base64.b64decode(raw.strip(),validate=True)
                if len(business_key)!=32:raise MigrationError('Invalid legacy business key; no new key generated')
                _write(key_path,encode_key(business_key,principal.issuer,principal.subject),job/'pending-writes')
                if rag_path.exists():
                    from rag.store import convert_legacy_store
                    from database.crypto import FieldCipher
                    pending=job/'pending-writes'/('rag-'+uuid.uuid4().hex+'.sqlite3')
                    convert_legacy_store(rag_path,pending,cipher=FieldCipher(business_key,'reviewed-migration'),owner=[principal.issuer,principal.subject],confirmed=True)
                    os.replace(pending,rag_path)
            elif rag_path.exists():
                raise MigrationError('RAG conversion requires reviewed existing business key')
            if replacement and replacement['mode']=='same-owner-format':
                _format_upgrade(stage,principal,plan['files'])
            state['hashes']={str(p.relative_to(stage)).replace('\\','/'):_sha(p.read_bytes()) for p in stage.rglob('*') if p.is_file()}
            if set(state['hashes'])!=set(plan['files']):raise MigrationError('Unexpected staging file')
            if replacement:state['directories']=_tree_snapshot(stage)['dirs']
            state['phase']='staging';_write(journal,cipher.encrypt(_json(state)));checkpoint('staging')
        if state['phase']=='staging':
            if stage.exists():
                _check_tree(stage,state['hashes'],state.get('directories'))
                if replacement:
                    if previous.exists():
                        _assert_snapshot(previous,replacement['snapshot'])
                    else:
                        _assert_snapshot(target,replacement['snapshot'])
                        _eligible_target(target,principal,plan)
                    marker=_safe(_transition_marker(target))
                    if marker.exists() and marker.read_bytes()!=plan['binding'].encode('ascii'):raise MigrationError('Other workspace transition pending')
                    _write(marker,plan['binding'].encode('ascii'),job/'pending-writes')
                    if not previous.exists():
                        _assert_snapshot(target,replacement['snapshot'])
                        os.rename(target,previous)
                        _assert_snapshot(previous,replacement['snapshot'])
                        checkpoint('archived')
                if target.exists():raise MigrationError('Destination already exists; both sides preserved')
                _safe(target.parent).mkdir(parents=True,exist_ok=True)
                os.rename(stage,target)
                checkpoint('promotion')
            _check_tree(target,state['hashes'],state.get('directories'))
            state['phase']='committed';_write(journal,cipher.encrypt(_json(state)));checkpoint('committed')
        _check_tree(target,state['hashes'],state.get('directories'))
        if replacement:
            _assert_snapshot(previous,replacement['snapshot'])
            _clear_transition_marker(target,plan['binding'])
        return state

def rollback(plan,principal,approval,key):
    with _transaction(plan,principal,approval,key) as (target,job,cipher):
        journal=job/'journal.enc';state=_read(journal,cipher)
        identity={'binding':plan['binding'],'issuer':principal.issuer,'subject':principal.subject}
        if state['identity']!=identity:raise MigrationError('Journal owner mismatch')
        _read(job/'backup.enc',cipher)
        if state['phase']=='rolled_back':
            if _replacement(plan,principal):
                _assert_snapshot(target,plan['replacement']['snapshot'])
                _clear_transition_marker(target,plan['binding'])
            return state
        archive=job/'rolled-back-workspace'
        replacement=_replacement(plan,principal);previous=job/'previous-workspace'
        if replacement:
            # Reject changed live data before recording rollback intent or blocking normal startup.
            if previous.exists():
                _assert_snapshot(previous,replacement['snapshot'])
                if target.exists():
                    _check_tree(target,state.get('hashes',{}),state.get('directories'))
                    if archive.exists():raise MigrationError('Rollback archive already exists')
                elif archive.exists():
                    _check_tree(archive,state.get('hashes',{}),state.get('directories'))
            else:
                _assert_snapshot(target,replacement['snapshot'])
            state['phase']='rolling_back';_write(journal,cipher.encrypt(_json(state)))
            marker=_safe(_transition_marker(target))
            if marker.exists() and marker.read_bytes()!=plan['binding'].encode('ascii'):raise MigrationError('Other workspace transition pending')
            _write(marker,plan['binding'].encode('ascii'),job/'pending-writes')
            if previous.exists():
                _assert_snapshot(previous,replacement['snapshot'])
                if target.exists():
                    _check_tree(target,state.get('hashes',{}),state.get('directories'))
                    if archive.exists():raise MigrationError('Rollback archive already exists')
                    os.rename(target,archive)
                os.rename(previous,target)
            _assert_snapshot(target,replacement['snapshot'])
            state['phase']='rolled_back';_write(journal,cipher.encrypt(_json(state)))
            _clear_transition_marker(target,plan['binding'])
            return state
        if target.exists():
            _check_tree(target,state.get('hashes',{}),state.get('directories'))
            if archive.exists():raise MigrationError('Rollback archive already exists')
            os.rename(target,archive)
        elif not archive.exists() and state['phase']=='committed':raise MigrationError('Committed workspace missing')
        state['phase']='rolled_back';_write(journal,cipher.encrypt(_json(state)))
        return state

def restore_backup(plan,principal,approval,key):
    """Recover into quarantine only, never overwrite source or an active workspace."""
    with _transaction(plan,principal,approval,key,source_required=False) as (_,job,cipher):
        payload=_read(job/'backup.enc',cipher)
        identity={'binding':plan['binding'],'issuer':principal.issuer,'subject':principal.subject}
        if payload['identity']!=identity or set(payload['files'])!=set(plan['files']):
            raise MigrationError('Backup binding/manifest mismatch')
        expected={_relative(v['source']):v['sha256'] for v in plan['files'].values()}
        recovered=_safe(job/'recovered-original');stage=_safe(job/'recovery-staging')
        if recovered.exists():
            _check_tree(recovered,expected)
            return recovered
        stage.mkdir(exist_ok=True)
        for name,encoded in payload['files'].items():
            data=base64.b64decode(encoded,validate=True)
            if _sha(data)!=plan['files'][name]['sha256']:raise MigrationError('Backup hash mismatch')
            _write(stage/_relative(plan['files'][name]['source']),data,job/'pending-writes')
        _check_tree(stage,expected)
        os.rename(stage,recovered)
        _check_tree(recovered,expected)
        return recovered
