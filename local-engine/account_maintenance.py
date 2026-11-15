"""Unexposed maintenance adapter: online identity + signed human plan approval.

NOT an HTTP route, first-login hook, CLI, or automatic legacy adoption. Trusted
launcher configuration and native protected key-vault transport are required;
never accept either from a WebView/HTTP caller. This module only verifies an
approval issued AFTER separate human ownership review. It cannot issue one.
No production approval service/key transport is enabled by adding this module.
"""
from dataclasses import dataclass
import base64
import json
import time
import re
from uuid import UUID
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from account_workspace import verify_central_identity
from account_migration import MigrationError, OwnerApproval, _validate, migrate, rollback, restore_backup

@dataclass(frozen=True)
class MaintenanceConfig:
    central_url: str
    publishable_key: str
    approval_keys: dict[str, str]

def _decode(value, limit):
    if not isinstance(value,str) or not 1 <= len(value) <= limit:
        raise MigrationError('Invalid maintenance approval encoding')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)

def _unique_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise MigrationError('Duplicate approval claim')
        value[key]=item
    return value

def _approval(token, principal, binding, operation, keys, now, installation_id, client_epoch, nonce):
    try:
        if not isinstance(token,dict) or set(token)!={'kid','payload','sig'}:
            raise ValueError()
        key=Ed25519PublicKey.from_public_bytes(_decode(keys[token['kid']],128))
        raw=_decode(token['payload'],8192)
        key.verify(_decode(token['sig'],128),raw)
        value=json.loads(raw,object_pairs_hook=_unique_object)
        expected={'v':2,'kid':token['kid'],'iss':principal.issuer,'sub':principal.subject,
                  'aud':'moadoom-maintenance','purpose':'legacy-owner-approval',
                  'binding':binding,'operation':operation,'installation_id':installation_id,'client_epoch':client_epoch,'nonce':nonce}
        if set(value)!=set(expected)|{'iat','exp','approval_id'} or any(value[k]!=v for k,v in expected.items()):
            raise ValueError()
        if str(UUID(value['approval_id']))!=value['approval_id'] or any(type(value[k]) is not int for k in ('v','iat','exp','client_epoch')):
            raise ValueError()
        if not 0 <= value['iat'] <= now < value['exp'] or not 0 < value['exp']-value['iat'] <= 300:
            raise ValueError()
    except Exception as error:
        raise MigrationError('Signed owner/plan/operation approval invalid or expired') from error
    return OwnerApproval(principal.issuer,principal.subject,binding)

def execute_reviewed_migration(plan, operation, access_token, approval_token, config, key_vault, *, installation_id=None, client_epoch=None, nonce=None, confirmed=False, now=None, opener=None):
    """Trusted maintenance-process entry only; all checks precede key access.

    key_vault(principal, binding, create) must use native OS-protected storage,
    retain the original key on retry, never export/log it, and fail if a recovery
    key is missing. The integration must preserve the native account epoch and
    operation lease. `now` and `opener` are isolated test seams, not request fields.
    Replaying the SAME still-valid approval only resumes its idempotent operation;
    another operation, account, installation, source or manifest needs new review.
    """
    operations={'migrate':migrate,'rollback':rollback,'restore_backup':restore_backup}
    if operation not in operations or not isinstance(config,MaintenanceConfig):
        raise MigrationError('Trusted maintenance configuration/operation required')
    if confirmed is not True or not isinstance(installation_id,str) or not re.fullmatch('[0-9a-f]{64}',installation_id) or not isinstance(nonce,str) or not re.fullmatch('[0-9a-f]{64}',nonce) or type(client_epoch) is not int or not 0<=client_epoch<=9007199254740991:
        raise MigrationError('Native installation/epoch and explicit confirmation required')
    try:
        principal=verify_central_identity(config.central_url,config.publishable_key,access_token,opener=opener)
    except Exception as error:
        raise MigrationError('Online identity verification required for maintenance') from error
    approval=_approval(approval_token,principal,plan.get('binding'),operation,config.approval_keys,int(time.time()) if now is None else now,installation_id,client_epoch,nonce)
    _validate(plan,principal,approval,source_required=operation!='restore_backup')
    key=key_vault(principal,plan['binding'],operation=='migrate')
    # No credential or key is included in the returned status/quarantine path.
    return operations[operation](plan,principal,approval,key)
