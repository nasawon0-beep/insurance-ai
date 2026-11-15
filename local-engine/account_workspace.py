"""Identity and path primitives; no automatic legacy ownership or migration."""
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
import hashlib,json,re,urllib.request

class IdentityUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class VerifiedPrincipal:
    issuer:str
    subject:str

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise IdentityUnavailable('Central authentication redirect refused')

def verify_central_identity(url, publishable_key, access_token, *, opener=None):
    """URL/key come from trusted build/launcher configuration, not an HTTP body.
    No offline fallback, email authority, decoded-only JWT, or persisted proof.
    opener is a dependency seam for isolated transport tests only.
    """
    if not isinstance(url,str) or not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co',url):
        raise IdentityUnavailable('Trusted central endpoint required')
    if not isinstance(publishable_key,str) or not publishable_key.startswith('sb_publishable_'):
        raise IdentityUnavailable('Public configuration required')
    if not isinstance(access_token,str) or not 1<=len(access_token)<=16384 or any(c in access_token for c in '\r\n'):
        raise IdentityUnavailable('Central session required')
    request=urllib.request.Request(url+'/auth/v1/user',headers={'apikey':publishable_key,'Authorization':'Bearer '+access_token})
    transport=opener or urllib.request.build_opener(NoRedirect())
    try:
        with transport.open(request,timeout=10) as response:
            if response.status!=200:raise IdentityUnavailable('Central session rejected')
            raw=response.read(65537)
            if len(raw)>65536:raise IdentityUnavailable('Central response too large')
            value=json.loads(raw)
            subject=value.get('id')
            if not isinstance(subject,str) or str(UUID(subject))!=subject:
                raise IdentityUnavailable('Immutable central subject required')
    except Exception as error:
        raise IdentityUnavailable('Online central identity verification failed') from error
    return VerifiedPrincipal(url+'/auth/v1',subject)

@dataclass(frozen=True)
class WorkspaceLayout:
    root:Path
    @property
    def database(self):return self.root/'data'/'customers.sqlite3'
    @property
    def rag_database(self):return self.root/'data'/'rag-index.sqlite3'
    @property
    def key(self):return self.root/'config'/'customer-db.key'
    @property
    def backups(self):return self.root/'data'/'backups'
    @property
    def pdfs(self):return self.root/'pdfs'
    @property
    def cache(self):return self.root/'cache'
    @property
    def mobile(self):return self.root/'config'/'mobile-sync'


def workspace_for(installation_root, principal):
    if not isinstance(principal,VerifiedPrincipal):raise IdentityUnavailable('Verified principal required')
    if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co/auth/v1',principal.issuer) or str(UUID(principal.subject))!=principal.subject:
        raise IdentityUnavailable('Invalid principal')
    digest=hashlib.sha256(json.dumps([principal.issuer,principal.subject],separators=(',',':')).encode()).hexdigest()
    base=Path(installation_root).resolve()
    candidate=base/'accounts'/digest
    # Reject symlinks/junctions rather than resolving into another account/root.
    for item in (base,base/'accounts',candidate):
        if item.is_symlink() or (item.exists() and getattr(item.stat(follow_symlinks=False),'st_file_attributes',0)&0x400):
            raise IdentityUnavailable('Reparse point workspace refused')
    return WorkspaceLayout(candidate)
