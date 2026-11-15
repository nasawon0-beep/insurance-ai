import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, copyFileSync, readFileSync, writeFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const names=['VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS','VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS'];
for (const scenario of ['same','reordered','mismatch-workspace','mismatch-maintenance','empty-workspace','empty-maintenance','invalid','empty-map']) {
  test(`repository maps actual exporter: ${scenario}`,()=>{
    const dir=mkdtempSync(path.join(tmpdir(),'release-public-map-'));
    mkdirSync(path.join(dir,'scripts'));mkdirSync(path.join(dir,'desktop'));
    copyFileSync(path.join(root,'scripts/build-moadoom-live.mjs'),path.join(dir,'scripts/build-moadoom-live.mjs'));
    const cfg=JSON.parse(readFileSync(path.join(root,'desktop/moadoom-live.public.json'),'utf8'));
    const map=JSON.stringify({'fixture-a':Buffer.alloc(32,1).toString('base64'),'fixture-b':Buffer.alloc(32,2).toString('base64')});
    for(const name of [...names,'VITE_MOADOOM_LICENSE_PUBLIC_KEYS']) cfg[name]=map;
    writeFileSync(path.join(dir,'desktop/moadoom-live.public.json'),JSON.stringify(cfg));
    const out=path.join(dir,'github-env');
    const env={...process.env,GITHUB_ENV:out,...Object.fromEntries(names.map(name=>[name,map]))};
    if(scenario==='reordered') env[names[0]]=JSON.stringify(Object.fromEntries(Object.entries(JSON.parse(map)).reverse()),null,2);
    if(scenario.startsWith('mismatch')) env[names[scenario.endsWith('workspace')?0:1]]=JSON.stringify({'fixture-a':Buffer.alloc(32,3).toString('base64')});
    if(scenario==='empty-workspace') env[names[0]]='';
    if(scenario==='empty-maintenance') delete env[names[1]];
    if(scenario==='invalid') env[names[0]]='not-json';
    if(scenario==='empty-map') env[names[0]]='{}';
    const result=spawnSync(process.execPath,[path.join(dir,'scripts/build-moadoom-live.mjs'),'github-env'],{env,encoding:'utf8'});
    const ok=['same','reordered'].includes(scenario);
    assert.equal(result.status,ok?0:2,result.stderr);
    assert.equal(existsSync(out),ok,'failure must not export any partial configuration');
    if(ok) for(const name of names) assert.ok(readFileSync(out,'utf8').includes(`${name}=${map}\n`),'committed map must remain canonical');
    for(const value of Object.values(JSON.parse(map))) assert.ok(!(result.stdout+result.stderr).includes(value),'map value must not reach logs');
  });
}
