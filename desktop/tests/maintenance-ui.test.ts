import test from 'node:test';
import assert from 'node:assert/strict';
import { maintenanceAllowed, MAINTENANCE_LIMIT } from '../src/maintenancePolicy.ts';
test('maintenance action needs native preparation, confirmation and idle state',()=>{
 assert.equal(maintenanceAllowed(true,true,true,false),true);
 for(const args of [[false,true,true,false],[true,false,true,false],[true,true,false,false],[true,true,true,true]])assert.equal(maintenanceAllowed(...args),false);
 assert.match(MAINTENANCE_LIMIT,/Windows 프로필/);assert.match(MAINTENANCE_LIMIT,/보장하지 않습니다/);assert.match(MAINTENANCE_LIMIT,/평문/);
});
