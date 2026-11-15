// Server-only workspace authority. No customer data, passwords or email claims.
import {b64Decode,b64urlEncode,canonicalJson} from './license.ts';
const hex64=/^[0-9a-f]{64}$/;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export type VerifiedInstallation={installation_id:string;installation_key:string;client_epoch:number;nonce:string;requested_at:number};
export type GrantRow={subject:string;installation_id:string;revocation_generation:number;sequence:number;issued_at:number};
export type WorkspaceGrant={v:1;kid:string;iss:string;sub:string;aud:'moadoom-desktop';purpose:'account-workspace';installation_id:string;client_epoch:number;nonce:string;jti:string;iat:number;nbf:number;exp:number;business_exp:number;revocation_generation:number;sequence:number};
function integer(n:unknown):n is number{return Number.isSafeInteger(n)&&Number(n)>=0;}
export async function verifyInstallationRequest(raw:unknown,issuer:string,subject:string,clock=Date.now()/1000):Promise<VerifiedInstallation>{
  if(!raw||typeof raw!=='object'||Array.isArray(raw)||!uuid.test(subject)||!/^https:\/\/[a-z0-9-]+\.supabase\.co\/auth\/v1$/.test(issuer))throw Error('invalid request');
  const r=raw as Record<string,unknown>;
  if(Object.keys(r).sort().join(',')!=='client_epoch,installation_key,nonce,proof,requested_at'||!integer(r.client_epoch)||!integer(r.requested_at)||Math.abs(r.requested_at-clock)>300||typeof r.installation_key!=='string'||!hex64.test(r.installation_key)||typeof r.nonce!=='string'||!hex64.test(r.nonce)||typeof r.proof!=='string'||r.proof.length>128)throw Error('invalid installation proof');
  const bytes=new Uint8Array(r.installation_key.match(/../g)!.map(n=>parseInt(n,16)));
  const installation_id=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  const key=await crypto.subtle.importKey('raw',bytes,'Ed25519',false,['verify']);
  const message={v:1,aud:'moadoom-workspace-issue',iss:issuer,sub:subject,installation_id,client_epoch:r.client_epoch,nonce:r.nonce,iat:r.requested_at};
  if(!await crypto.subtle.verify('Ed25519',key,b64Decode(r.proof),new TextEncoder().encode(canonicalJson(message))))throw Error('invalid installation proof');
  return {installation_id,installation_key:r.installation_key,client_epoch:r.client_epoch,nonce:r.nonce,requested_at:r.requested_at};
}
export function buildWorkspaceGrant(request:VerifiedInstallation,row:GrantRow,ent:{user_id:string},issuer:string,kid:string,clock:number):WorkspaceGrant{
  // Deadline starts at the atomic DB authorization decision, not delayed HTTP signing.
  const now=row.issued_at;
  if(!integer(now)||Math.abs(clock-now)>300)throw Error('stale issuance decision');
  if(!integer(now)||!kid||kid.length>128||!/^https:\/\/[a-z0-9-]+\.supabase\.co\/auth\/v1$/.test(issuer)||!uuid.test(row.subject)||row.subject!==ent.user_id||row.installation_id!==request.installation_id||!integer(row.revocation_generation)||!integer(row.sequence)||row.sequence<1)throw Error('workspace authority unavailable');
  // Ownership is authenticated independently of billing. Keep the existing banner-only
  // subscription policy; business_exp is the ownership deadline, never subscription end.
  const exp=now+5*86400;
  const business_exp=exp;
  return {v:1,kid,iss:issuer,sub:row.subject,aud:'moadoom-desktop',purpose:'account-workspace',installation_id:request.installation_id,client_epoch:request.client_epoch,nonce:request.nonce,jti:crypto.randomUUID(),iat:now,nbf:now,exp,business_exp,revocation_generation:row.revocation_generation,sequence:row.sequence};
}
export async function signWorkspaceGrant(grant:WorkspaceGrant,key:CryptoKey){
  const raw=new TextEncoder().encode(canonicalJson(grant));
  return {kid:grant.kid,payload:b64urlEncode(raw),sig:b64urlEncode(new Uint8Array(await crypto.subtle.sign('Ed25519',key,raw)))};
}
