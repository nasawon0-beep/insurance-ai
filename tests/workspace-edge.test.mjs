// Executes the real Edge handler with synthetic Auth/RPC transport, NOT deployed Supabase evidence.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from '../desktop/node_modules/esbuild/lib/main.js';
const require=createRequire(new URL('../desktop/package.json',import.meta.url));
const A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const url='https://fixture.supabase.co';
const canonical=v=>JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])));
const b64=bytes=>Buffer.from(bytes).toString('base64url');

test('real Edge handler: caller-scoped RPC, signed grants, denied users, replay and install revocation',async()=>{
 const server=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
 const env={SUPABASE_URL:url,SUPABASE_ANON_KEY:'sb_publishable_fixture_only',MOADOOM_WORKSPACE_KID:'synthetic',MOADOOM_WORKSPACE_SIGNING_KEY:b64(await crypto.subtle.exportKey('pkcs8',server.privateKey))};
 let handler;const originalDeno=globalThis.Deno,originalFetch=globalThis.fetch;
 let revoked=false,denied=false,billingExpired=false;const sequences=new Map(),nonces=new Set();let calls=0;
 globalThis.Deno={env:{get:k=>env[k]},serve:fn=>{handler=fn;}};
 globalThis.fetch=async(input,options)=>{
  const req=new Request(input,options);assert.ok(req.url.startsWith(url+'/'),'No external network allowed');
  const token=req.headers.get('authorization');const subject=token==='Bearer fixture-A'?A:token==='Bearer fixture-B'?B:null;
  const json=(status,value)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  if(req.url===url+'/auth/v1/user')return subject&&!denied?json(200,{id:subject,email_confirmed_at:'2030-01-01T00:00:00Z'}):json(401,{msg:'invalid fixture login'});
  assert.ok(subject,'RPC must retain the authenticated caller, not a service role');calls++;
  if(req.url===url+'/rest/v1/rpc/moadoom_my_entitlement')return json(200,{user_id:subject,role:null,effective_status:billingExpired?'expired':'active',subscription:{kind:'term',ends_at:new Date(Date.now()+86400000).toISOString()}});
  if(req.url===url+'/rest/v1/rpc/moadoom_issue_workspace_metadata'){
   const body=await req.json();const marker=subject+'|'+body.p_installation_id;const nonce=marker+'|'+body.p_nonce;
   if(revoked||nonces.has(nonce))return json(400,{code:'P0001',message:'fixture revoked or replay'});
   nonces.add(nonce);const sequence=(sequences.get(marker)??0)+1;sequences.set(marker,sequence);
   return json(200,{subject,installation_id:body.p_installation_id,revocation_generation:0,sequence,issued_at:Math.floor(Date.now()/1000)});
  }
  throw new Error('Unexpected fixture transport URL');
 };
 try{
  const output=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/moadoom-workspace/index.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',plugins:[{name:'fixture-supabase',setup(b){b.onResolve({filter:/^jsr:@supabase\/supabase-js@2$/},()=>({path:require.resolve('@supabase/supabase-js')}));}}]});
  const Module=require('node:module');const filename=fileURLToPath(new URL('../desktop/edge-fixture.cjs',import.meta.url));const module=new Module(filename);module.filename=filename;module.paths=Module._nodeModulePaths(fileURLToPath(new URL('../desktop',import.meta.url)));module._compile(output.outputFiles[0].text,filename);
  assert.equal(typeof handler,'function');
  async function request(subject=A){
   const pair=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);const pub=Buffer.from(await crypto.subtle.exportKey('raw',pair.publicKey));
   const id=Buffer.from(await crypto.subtle.digest('SHA-256',pub)).toString('hex');const nonce=Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString('hex');const requested_at=Math.floor(Date.now()/1000);
   const payload={v:1,aud:'moadoom-workspace-issue',iss:url+'/auth/v1',sub:subject,installation_id:id,client_epoch:1,nonce,iat:requested_at};
   return {installation_key:pub.toString('hex'),client_epoch:1,nonce,requested_at,proof:b64(await crypto.subtle.sign('Ed25519',pair.privateKey,new TextEncoder().encode(canonical(payload))))};
  }
  const call=(body,token='fixture-A')=>handler(new Request(url+'/functions/v1/moadoom-workspace',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)}));
  const body=await request();const response=await call(body);assert.equal(response.status,200);const {grant}=await response.json();
  assert.equal(await crypto.subtle.verify('Ed25519',server.publicKey,Buffer.from(grant.sig,'base64url'),Buffer.from(grant.payload,'base64url')),true);
  const claims=JSON.parse(Buffer.from(grant.payload,'base64url').toString());assert.equal(claims.sub,A);assert.equal(claims.iss,url+'/auth/v1');assert.equal(claims.exp-claims.iat,5*86400);assert.equal(claims.business_exp,claims.exp);
  assert.equal((await call(body)).status,403,'used nonce is not reissued');
  assert.equal((await call(await request(),'fixture-B')).status,400,'A proof cannot become B authority');
  assert.equal((await call(await request(),'invalid')).status,401);
  billingExpired=true;assert.equal((await call(await request())).status,200,'expired billing preserves ownership');billingExpired=false;
  denied=true;assert.equal((await call(await request())).status,401);denied=false;
  revoked=true;assert.equal((await call(await request())).status,403);revoked=false;
  assert.equal((await call({...await request(),sub:B})).status,400);
  assert.equal((await call(await request(B),'fixture-B')).status,200);
  assert.ok(calls>0);
 }finally{globalThis.fetch=originalFetch;globalThis.Deno=originalDeno;}
});
