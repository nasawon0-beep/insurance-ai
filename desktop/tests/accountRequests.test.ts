import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createAccountFence} from '../src/accountRequests.ts';
test('account change invalidates pending responses even after switching back',async()=>{
 const f=createAccountFence(); const a=f.capture();f.invalidate();const b=f.capture();
 assert.throws(()=>f.assert(a));f.assert(b);f.invalidate();assert.throws(()=>f.assert(a));assert.throws(()=>f.assert(b));
});
test('late response body is rejected, not only response headers',async()=>{
 const f=createAccountFence();const epoch=f.capture();const r=f.response(new Response('{"private":"A"}'),epoch);
 f.invalidate();await assert.rejects(()=>r.json());await assert.rejects(()=>r.clone().text());
});
