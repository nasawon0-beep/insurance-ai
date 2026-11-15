import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
const w=readFileSync(new URL('../.github/workflows/release.yml',import.meta.url),'utf8');
test('CI checks account-bound engine modules and the actual shipping worker',()=>{
 for(const name of ['account_boundary','authority_clock','protected_business_key','rag.store']) assert.ok(w.includes("'"+name+"'"),'missing archive gate: '+name);
 assert.match(w,/python\.exe -m pytest -q tests\/test_packaged_maintenance\.py/);
});
test('CI opts legacy business suites into account-bound fixture without changing Auth suites',()=>{
 const lines=w.split('\n').filter(x=>x.includes('-p account_bound_ci'));
 assert.equal(lines.length,2);
 assert.ok(lines.every(x=>x.includes('PYTHONPATH=tests')));
 assert.ok(lines.every(x=>!x.includes('test_account_isolation_boundary')));
});
