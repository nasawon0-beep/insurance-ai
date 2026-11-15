import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
const workflow=readFileSync(new URL('../.github/workflows/release.yml',import.meta.url),'utf8').replace(/\r\n/g,'\n');
test('CI builds pinned shipping and separate QA workers before native validation',()=>{
 const shipping=workflow.indexOf('python.exe scripts/build_maintenance_worker.py\n');
 assert.ok(shipping>0&&shipping<workflow.indexOf('cargo check'));
 assert.match(workflow,/build_maintenance_worker\.py --qa-fixture/);
 assert.match(workflow,/MAINTENANCE_TEST_PACKAGED_WORKER/);
});
test('CI wires approved public verifier maps and existing signer key without password override',()=>{
 assert.match(workflow,/VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS: \$\{\{ vars\.VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS \}\}/);
 assert.match(workflow,/VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS: \$\{\{ vars\.VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS \}\}/);
 assert.match(workflow,/TAURI_SIGNING_PRIVATE_KEY: \$\{\{ secrets\.TAURI_SIGNING_PRIVATE_KEY \}\}/);
 assert.doesNotMatch(workflow,/TAURI_SIGNING_PRIVATE_KEY_PASSWORD/);
});
