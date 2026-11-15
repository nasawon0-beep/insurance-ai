// Candidate only. Signer lives ONLY in the reviewed server deployment.
import {createClient} from 'jsr:@supabase/supabase-js@2';
import {importSigningKey,b64urlEncode,canonicalJson} from '../_shared/license.ts';
import {verifyMaintenanceRequest,buildMaintenanceApproval} from '../_shared/maintenance.ts';
const response=(status:number,value:unknown)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
Deno.serve(async req=>{
 if(req.method!=='POST')return response(405,{error:'method_not_allowed'});
 // No CORS approval path. Native caller only; authentication remains mandatory.
 const authorization=req.headers.get('Authorization')??'';
 if(!authorization.startsWith('Bearer ')||authorization.length>17000)return response(401,{error:'login_required'});
 try{
  const url=Deno.env.get('SUPABASE_URL')??'',pub=Deno.env.get('SUPABASE_ANON_KEY');
  const kid=Deno.env.get('MOADOOM_MAINTENANCE_KID'),secret=Deno.env.get('MOADOOM_MAINTENANCE_SIGNING_KEY');
  if(!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url)||!pub||!kid||!secret)return response(503,{error:'not_configured'});
  const client=createClient(url,pub,{global:{headers:{Authorization:authorization}},auth:{persistSession:false,autoRefreshToken:false}});
  const {data,error}=await client.auth.getUser(authorization.slice(7));
  if(error||!data.user)return response(401,{error:'login_required'});
  if(!data.user.email_confirmed_at)return response(403,{error:'identity_not_confirmed'});
  const reader=req.body?.getReader();if(!reader)return response(400,{error:'request_required'});
  let size=0,text='';const decoder=new TextDecoder();
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>8192){await reader.cancel();return response(413,{error:'too_large'});}text+=decoder.decode(value,{stream:true});}text+=decoder.decode();
  let request;try{request=await verifyMaintenanceRequest(JSON.parse(text),url+'/auth/v1',data.user.id,Date.now()/1000);}catch{return response(400,{error:'invalid_confirmation_proof'});}
  const {data:row,error:denied}=await client.rpc('moadoom_consume_maintenance_review',{p_installation_id:request.installation_id,p_client_epoch:request.client_epoch,p_binding:request.binding,p_operation:request.operation,p_nonce:request.nonce});
  if(denied||!row)return response(403,{error:'independent_review_required'});
  const claims=buildMaintenanceApproval(request,row,kid,Date.now()/1000),key=await importSigningKey(secret);
  const raw=new TextEncoder().encode(canonicalJson(claims));
  return response(200,{approval:{kid,payload:b64urlEncode(raw),sig:b64urlEncode(new Uint8Array(await crypto.subtle.sign('Ed25519',key,raw)))}});
 }catch{return response(503,{error:'maintenance_unavailable'});}
});
