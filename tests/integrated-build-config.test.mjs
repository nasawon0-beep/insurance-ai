import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {validateIntegratedConfig} from '../scripts/build-moadoom-live.mjs';
const cfg={VITE_MOADOOM_SUPABASE_URL:'https://abcdefghijklmnopqrst.supabase.co',VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY:'sb_publishable_abcdefghijklmnopqrstuv',VITE_MOADOOM_LICENSE_PUBLIC_KEYS:JSON.stringify({license:Buffer.alloc(32,1).toString('base64')}),VITE_MOADOOM_SITE_URL:'https://example.test/site/',VITE_MOADOOM_SIGNUP_OPEN:'false'};
test('legacy central configuration alone cannot build integrated workspace app',()=>{
 assert.ok(validateIntegratedConfig(cfg).some(e=>e.includes('VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS')));
});
test('workspace public config passes; optional maintenance stays separately guarded',()=>{
 const valid={...cfg,VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS:JSON.stringify({workspace:Buffer.alloc(32,2).toString('base64')})};
 assert.deepEqual(validateIntegratedConfig(valid),[]);
 for(const value of ['{}','[]','null','{"bad":"AAAA"}','{"bad":"PRIVATE KEY"}'])assert.ok(validateIntegratedConfig({...valid,VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS:value}).length);
 assert.ok(validateIntegratedConfig({...valid,VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS:'{}'}).length);
});
test('check-env accepts only explicitly injected complete synthetic public config',()=>{
 const env={...process.env,...cfg,VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS:JSON.stringify({workspace:Buffer.alloc(32,2).toString('base64')})};
 delete env.VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS;
 const run=spawnSync(process.execPath,['scripts/build-moadoom-live.mjs','check-env'],{cwd:new URL('..',import.meta.url),env,encoding:'utf8'});
 assert.equal(run.status,0,run.stderr);
});

test('ordinary npm build refuses missing injected public env before writing dist',()=>{
 const pkg=JSON.parse(readFileSync(new URL('../desktop/package.json',import.meta.url),'utf8'));
 assert.equal(pkg.scripts.prebuild,'node ../scripts/build-moadoom-live.mjs check-env');
 const env={...process.env};for(const k of Object.keys(env))if(k.startsWith('VITE_MOADOOM_'))delete env[k];
 const run=spawnSync(process.execPath,['scripts/build-moadoom-live.mjs','check-env'],{cwd:new URL('..',import.meta.url),env,encoding:'utf8'});
 assert.equal(run.status,2);assert.match(run.stderr,/VITE_MOADOOM_SUPABASE_URL/);
});
