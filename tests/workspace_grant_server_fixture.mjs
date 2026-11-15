// Test-only stdin/stdout interoperability fixture. Synthetic signing key never leaves memory.
import {verifyInstallationRequest,buildWorkspaceGrant,signWorkspaceGrant} from '../supabase/functions/_shared/workspace.ts';
let text='';for await(const chunk of process.stdin){text+=chunk;if(text.length>8192)throw Error('fixture input limit');}
const {request,subject,issuer,now}=JSON.parse(text);
if(issuer!=='https://fixture.supabase.co/auth/v1')throw Error('fixture issuer only');
const verified=await verifyInstallationRequest(request,issuer,subject,now);
const key=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
const claims=buildWorkspaceGrant(verified,{subject,installation_id:verified.installation_id,revocation_generation:0,sequence:1,issued_at:now},{user_id:subject,role:null,effective_status:'active',subscription:{kind:'term',ends_at:new Date((now+86400)*1000).toISOString()}},issuer,'synthetic-cross-language',now);
process.stdout.write(JSON.stringify({public_key:Buffer.from(await crypto.subtle.exportKey('raw',key.publicKey)).toString('base64url'),grant:await signWorkspaceGrant(claims,key.privateKey)}));
