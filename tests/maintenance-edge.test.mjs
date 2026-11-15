import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../desktop/node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
test('real maintenance handler never signs without fresh Auth and independent review',async()=>{
 const pair=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
 const b64=v=>Buffer.from(v).toString('base64url');
 const env={SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_ANON_KEY:'sb_publishable_fixture',MOADOOM_MAINTENANCE_KID:'review',MOADOOM_MAINTENANCE_SIGNING_KEY:b64(await crypto.subtle.exportKey('pkcs8',pair.privateKey))};
 const sub='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';let handler,authorized=true,reviewed=false,used=false;
 const previousDeno=globalThis.Deno,previousClient=globalThis.maintenanceClient;
 globalThis.Deno={env:{get:k=>env[k]},serve:fn=>handler=fn};
 globalThis.maintenanceClient={auth:{getUser:async()=>({data:{user:authorized?{id:sub,email_confirmed_at:'synthetic'}:null},error:null})},rpc:async(name,p)=>{
  assert.equal(name,'moadoom_consume_maintenance_review');if(!reviewed||used)return {data:null,error:{message:'denied'}};used=true;
  const now=Math.floor(Date.now()/1000);return {data:{approval_id:'cccccccc-cccc-4ccc-8ccc-cccccccccccc',subject:sub,installation_id:p.p_installation_id,client_epoch:p.p_client_epoch,binding:p.p_binding,operation:p.p_operation,nonce:p.p_nonce,issued_at:now,expires_at:now+300},error:null};
 }};
 try{
  const result=await build({entryPoints:[fileURLToPath(new URL('../supabase/functions/moadoom-maintenance/index.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',plugins:[{name:'isolated-auth-transport',setup(b){b.onResolve({filter:/^jsr:/},()=>({path:'fixture',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export const createClient=()=>globalThis.maintenanceClient;',loader:'js'}));}}]});
  const Module=require('node:module'),module=new Module('maintenance-fixture');module._compile(result.outputFiles[0].text,'maintenance-fixture.cjs');
  const installation=await crypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);const pub=Buffer.from(await crypto.subtle.exportKey('raw',installation.publicKey));
  const claims={v:2,aud:'moadoom-maintenance-request',iss:env.SUPABASE_URL+'/auth/v1',sub,installation_id:Buffer.from(await crypto.subtle.digest('SHA-256',pub)).toString('hex'),client_epoch:4,nonce:'a'.repeat(64),iat:Math.floor(Date.now()/1000),binding:'b'.repeat(64),operation:'migrate',confirmed:true};
  const raw=new TextEncoder().encode(JSON.stringify(Object.fromEntries(Object.entries(claims).sort(([a],[b])=>a.localeCompare(b)))));
  const body={...claims,installation_key:pub.toString('hex'),proof:b64(await crypto.subtle.sign('Ed25519',installation.privateKey,raw))};
  const call=(value=body)=>handler(new Request(env.SUPABASE_URL,{method:'POST',headers:{Authorization:'Bearer fixture'},body:JSON.stringify(value)}));
  assert.equal((await call()).status,403);reviewed=true;authorized=false;assert.equal((await call()).status,401);authorized=true;
  assert.equal((await call({...body,confirmed:false})).status,400);assert.equal(used,false);
  const response=await call();assert.equal(response.status,200);const {approval}=await response.json();
  assert.equal(await crypto.subtle.verify('Ed25519',pair.publicKey,Buffer.from(approval.sig,'base64url'),Buffer.from(approval.payload,'base64url')),true);
  assert.equal(JSON.parse(Buffer.from(approval.payload,'base64url')).client_epoch,4);
  assert.equal((await call()).status,403);
 }finally{globalThis.Deno=previousDeno;globalThis.maintenanceClient=previousClient;}
});
