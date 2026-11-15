// Generated Dashboard bundles + real SDK and real protected test signer bytes.
// All network is synthetic. Never use production credentials with this fixture.
import {readFileSync} from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';import {join} from 'node:path';
let input='';for await(const c of process.stdin) input+=c;const keys=JSON.parse(input);input='';
const issuer='https://fixture.supabase.co',sub='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const canonical=v=>JSON.stringify(Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))));
const b64=v=>Buffer.from(v).toString('base64url');
const results=[];
for(const purpose of ['workspace','maintenance']){
 const entry=keys[purpose];const prefix='MOADOOM_'+purpose.toUpperCase();
 const env={SUPABASE_URL:issuer,SUPABASE_ANON_KEY:'sb_publishable_synthetic_only',[prefix+'_KID']:entry.kid,[prefix+'_SIGNING_KEY']:entry.pkcs8};
 let handler,authorized=true,confirmed=true,reviewed=false,used=false,calls=0;
 const fetch=async(input,options)=>{
  const req=new Request(input,options);assert.ok(req.url.startsWith(issuer+'/'),'Network escape refused');
  const response=(status,obj)=>new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}});
  if(req.url===issuer+'/auth/v1/user')return authorized?response(200,{id:sub,email_confirmed_at:confirmed?'2030-01-01T00:00:00Z':null}):response(401,{msg:'synthetic denied'});
  assert.equal(req.headers.get('authorization'),'Bearer synthetic-user');calls++;
  const p=await req.json();
  if(purpose==='workspace'){
   assert.equal(req.url,issuer+'/rest/v1/rpc/moadoom_issue_workspace_metadata');
   if(used)return response(403,{message:'synthetic replay'});used=true;
   return response(200,{subject:sub,installation_id:p.p_installation_id,sequence:1,revocation_generation:0,issued_at:Math.floor(Date.now()/1000)});
  }
  assert.equal(req.url,issuer+'/rest/v1/rpc/moadoom_consume_maintenance_review');
  if(!reviewed||used)return response(403,{message:'synthetic independent review required'});used=true;
  const now=Math.floor(Date.now()/1000);
  return response(200,{approval_id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',subject:sub,installation_id:p.p_installation_id,client_epoch:p.p_client_epoch,binding:p.p_binding,operation:p.p_operation,nonce:p.p_nonce,issued_at:now,expires_at:now+300});
 };
 const context={Deno:{env:{get:k=>env[k]},version:{deno:'2.9.6'},serve:fn=>handler=fn},fetch,Request,Response,Headers,URL,URLSearchParams,TextEncoder,TextDecoder,crypto,atob,btoa,WebSocket,AbortController,AbortSignal,Blob,FormData,ReadableStream,structuredClone,setTimeout,clearTimeout,setInterval,clearInterval,console:{warn(){},log(){},error(){}}};
 vm.runInNewContext(readFileSync(join(process.argv[2],'moadoom-'+purpose+'.js'),'utf8'),context,{timeout:5000});
 assert.equal(typeof handler,'function');
 const pair=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);const pub=Buffer.from(await crypto.subtle.exportKey('raw',pair.publicKey));const id=Buffer.from(await crypto.subtle.digest('SHA-256',pub)).toString('hex'),nonce='a'.repeat(64),now=Math.floor(Date.now()/1000);
 const claims=purpose==='workspace'?{v:1,aud:'moadoom-workspace-issue',iss:issuer+'/auth/v1',sub,installation_id:id,client_epoch:1,nonce,iat:now}:{v:2,aud:'moadoom-maintenance-request',iss:issuer+'/auth/v1',sub,installation_id:id,client_epoch:1,nonce,iat:now,binding:'b'.repeat(64),operation:'migrate',confirmed:true};
 const proof=b64(await crypto.subtle.sign('Ed25519',pair.privateKey,new TextEncoder().encode(canonical(claims))));
 const body=purpose==='workspace'?{installation_key:pub.toString('hex'),client_epoch:1,nonce,requested_at:now,proof}:{...claims,installation_key:pub.toString('hex'),proof};
 const call=()=>handler(new Request(issuer+'/functions/v1/moadoom-'+purpose,{method:'POST',headers:{Authorization:'Bearer synthetic-user'},body:JSON.stringify(body)}));
 authorized=false;assert.equal((await call()).status,401);authorized=true;confirmed=false;assert.equal((await call()).status,403);confirmed=true;
 if(purpose==='maintenance'){assert.equal((await call()).status,403);reviewed=true;}
 const response=await call();assert.equal(response.status,200);const data=await response.json();const signed=data.grant??data.approval;
 const verify=await crypto.subtle.importKey('raw',Buffer.from(entry.public[entry.kid],'base64'),'Ed25519',false,['verify']);
 assert.equal(await crypto.subtle.verify('Ed25519',verify,Buffer.from(signed.sig,'base64url'),Buffer.from(signed.payload,'base64url')),true);
 assert.equal(JSON.parse(Buffer.from(signed.payload,'base64url')).sub,sub);
 assert.equal((await call()).status,403);assert.ok(calls>0);
 results.push({purpose,dpapi_to_dashboard_signature:true,auth_denial:true,email_confirmation_required:true,replay_denied:true});
}
console.log(JSON.stringify({synthetic_transport:true,results}));
