import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('login and account panel have no legacy-account login or linking UI',()=>{
 for(const file of ['LoginScreen.tsx','CentralAccountPanel.tsx']) {
 const text=readFileSync(new URL('../src/'+file,import.meta.url),'utf8');
 assert.ok(!text.includes('이 PC 기존 계정'));assert.ok(!text.includes('linkLocalAccount'));assert.ok(!text.includes('resetLocalPassword'));
 }
});
