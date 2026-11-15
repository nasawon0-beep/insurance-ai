import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('both Windows mobile child launches bind DPAPI owner from verified session',()=>{
 const source=readFileSync(new URL('../desktop/src-tauri/src/mobile_sync.rs',import.meta.url),'utf8');
 for(const name of ['admin_output','start_with_intent']){
  const start=source.indexOf('async fn '+name+'(');assert.ok(start>=0);
  const end=source.indexOf('\nasync fn ',start+1);
  const body=source.slice(start,end<0?undefined:end);
  assert.match(body,/\.env\("MOADOOM_ACCOUNT_ISSUER",\s*&session\.issuer\)/,name);
  assert.match(body,/\.env\("MOADOOM_ACCOUNT_SUBJECT",\s*&session\.subject\)/,name);
 }
});
