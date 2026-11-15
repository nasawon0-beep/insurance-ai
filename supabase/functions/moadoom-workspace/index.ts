// Candidate Edge function. Uses caller JWT, never service_role. Deploy separately after review.
import {createClient} from 'jsr:@supabase/supabase-js@2';
import {importSigningKey} from '../_shared/license.ts';
import {verifyInstallationRequest,buildWorkspaceGrant,signWorkspaceGrant} from '../_shared/workspace.ts';
const origins=new Set((Deno.env.get('MOADOOM_ALLOWED_ORIGINS')??'tauri://localhost,http://tauri.localhost,https://tauri.localhost').split(','));
function respond(req:Request,status:number,value:unknown){
  const origin=req.headers.get('Origin')??'';
  const headers:Record<string,string>={'Content-Type':'application/json','Cache-Control':'no-store','Vary':'Origin'};
  if(origins.has(origin))Object.assign(headers,{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'authorization,apikey,content-type,x-client-info','Access-Control-Allow-Methods':'POST,OPTIONS'});
  return new Response(status===204?null:JSON.stringify(value),{status,headers});
}
let signer:Promise<CryptoKey>|null=null;
Deno.serve(async(req)=>{
  if(req.method==='OPTIONS')return respond(req,204,null);
  if(req.method!=='POST')return respond(req,405,{error:'method_not_allowed'});
  const authorization=req.headers.get('Authorization')??'';
  if(!authorization.startsWith('Bearer ')||authorization.length>17000)return respond(req,401,{error:'login_required'});
  try{
    const url=Deno.env.get('SUPABASE_URL')??'';
    if(!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url))return respond(req,503,{error:'issuer_not_configured'});
    const anon=Deno.env.get('SUPABASE_ANON_KEY')||Object.values(JSON.parse(Deno.env.get('SUPABASE_PUBLISHABLE_KEYS')??'{}'))[0] as string;
    if(!anon)return respond(req,503,{error:'public_config_required'});
    const client=createClient(url,anon,{global:{headers:{Authorization:authorization}},auth:{persistSession:false,autoRefreshToken:false}});
    const {data,error}=await client.auth.getUser(authorization.slice(7));
    if(error||!data.user)return respond(req,401,{error:'login_required'});
    if(!data.user.email_confirmed_at)return respond(req,403,{error:'email_not_confirmed'});
    const reader=req.body?.getReader();if(!reader)return respond(req,400,{error:'invalid_request'});
    let text='';let size=0;const decoder=new TextDecoder();
    while(true){const chunk=await reader.read();if(chunk.done)break;size+=chunk.value.length;if(size>8192){await reader.cancel();return respond(req,413,{error:'request_too_large'});}text+=decoder.decode(chunk.value,{stream:true});}
    text+=decoder.decode();
    let request;try{request=await verifyInstallationRequest(JSON.parse(text),url+'/auth/v1',data.user.id);}catch{return respond(req,400,{error:'invalid_installation_proof'});}
    // getUser above establishes ownership; installation RPC enforces its revocation.
    // Billing status remains a separate banner policy, never an ownership permission.
    const ent={user_id:data.user.id};
    const kid=Deno.env.get('MOADOOM_WORKSPACE_KID');
    const pkcs8=Deno.env.get('MOADOOM_WORKSPACE_SIGNING_KEY');
    if(!kid||!pkcs8)return respond(req,503,{error:'workspace_signing_not_configured'});
    const {data:row,error:rowError}=await client.rpc('moadoom_issue_workspace_metadata',{p_installation_id:request.installation_id,p_installation_key:request.installation_key,p_nonce:request.nonce,p_requested_at:request.requested_at});
    if(rowError||!row)return respond(req,403,{error:'installation_not_authorized'});
    const grant=buildWorkspaceGrant(request,row,ent,url+'/auth/v1',kid,Date.now()/1000);
    signer??=importSigningKey(pkcs8);
    return respond(req,200,{grant:await signWorkspaceGrant(grant,await signer)});
  }catch{return respond(req,503,{error:'workspace_authority_unavailable'});}
});
