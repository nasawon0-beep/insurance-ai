// Source-order regression, not a rendered Tauri E2E test.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
test('startup gates engine launch on login and native workspace authority',()=>{
 const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
 const boot=app.slice(app.indexOf('const boot = useCallback'),app.indexOf('const waitForEngineHealth'));
 const launch=boot.indexOf('await startBackendWithLoading()');
 assert.ok(launch>=0);
 assert.ok(boot.indexOf('if (!isSignedIn())')>=0 && boot.indexOf('if (!isSignedIn())')<launch,'signed-out startup must not launch a business engine');
 assert.ok(boot.indexOf('await activateAccountWorkspace()')>=0 && boot.indexOf('await activateAccountWorkspace()')<launch,'restart must verify or resume signed authority before launching');
});
