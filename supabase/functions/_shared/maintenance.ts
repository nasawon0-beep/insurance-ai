// Separate authority: a user proof is necessary but NEVER sufficient to approve legacy ownership.
import {b64Decode,canonicalJson} from './license.ts';
const hex=/^[0-9a-f]{64}$/, uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const operations=new Set(['migrate','rollback','restore_backup']);
export type MaintenanceRequest={v:2;aud:string;iss:string;sub:string;installation_id:string;client_epoch:number;nonce:string;iat:number;binding:string;operation:string;confirmed:true;installation_key:string;proof:string};
export async function verifyMaintenanceRequest(raw:unknown,issuer:string,subject:string,now:number):Promise<MaintenanceRequest>{
 const r=raw as MaintenanceRequest;
 if(!r||Object.keys(r).sort().join(',')!=='aud,binding,client_epoch,confirmed,iat,installation_id,installation_key,iss,nonce,operation,proof,sub,v'||r.v!==2||r.aud!=='moadoom-maintenance-request'||r.iss!==issuer||r.sub!==subject||!uuid.test(subject)||!/^https:\/\/[a-z0-9-]+\.supabase\.co\/auth\/v1$/.test(issuer)||!hex.test(r.installation_id)||!hex.test(r.installation_key)||!hex.test(r.binding)||!hex.test(r.nonce)||!Number.isSafeInteger(r.client_epoch)||r.client_epoch<0||!Number.isSafeInteger(r.iat)||Math.abs(r.iat-now)>300||r.confirmed!==true||!operations.has(r.operation)||typeof r.proof!=='string'||r.proof.length>128)throw Error('invalid maintenance request');
 const bytes=new Uint8Array(r.installation_key.match(/../g)!.map(n=>parseInt(n,16)));
 const id=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
 if(id!==r.installation_id)throw Error('installation mismatch');
 const {installation_key,proof,...claims}=r;
 const key=await crypto.subtle.importKey('raw',bytes,'Ed25519',false,['verify']);
 if(!await crypto.subtle.verify('Ed25519',key,b64Decode(proof),new TextEncoder().encode(canonicalJson(claims))))throw Error('invalid installation proof');
 return r;
}
export function buildMaintenanceApproval(r:MaintenanceRequest,row:any,kid:string,now:number){
 if(!kid||kid.length>128||!row||!uuid.test(row.approval_id)||row.subject!==r.sub||row.installation_id!==r.installation_id||row.client_epoch!==r.client_epoch||row.binding!==r.binding||row.operation!==r.operation||row.nonce!==r.nonce||!Number.isSafeInteger(row.issued_at)||!Number.isSafeInteger(row.expires_at)||row.issued_at>now||now>=row.expires_at||row.expires_at-row.issued_at>300||now-row.issued_at>300)throw Error('independent reviewed approval required');
 return {v:2,kid,iss:r.iss,sub:r.sub,aud:'moadoom-maintenance',purpose:'legacy-owner-approval',binding:r.binding,operation:r.operation,installation_id:r.installation_id,client_epoch:r.client_epoch,nonce:r.nonce,approval_id:row.approval_id,iat:row.issued_at,exp:row.expires_at};
}
