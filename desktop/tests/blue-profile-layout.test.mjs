import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const customers=fs.readFileSync(new URL('../src/Customers.tsx',import.meta.url),'utf8');
const css=fs.readFileSync(new URL('../src/styles/blue.css',import.meta.url),'utf8');
test('profile exposes the three existing workflows through approved shortcut layout',()=>{
 assert.match(customers,/className="customer-shortcuts"/);
 for(const tab of ['policies','coverage','consultations']) assert.ok(customers.includes(`onClick={() => setActiveTab("${tab}")}`));
 assert.match(customers,/className="customer-overview"/);
 assert.match(customers,/detail\.first_registered_ym/);
 assert.match(css,/\.customer-shortcuts\s*\{[^}]*grid-template-columns:\s*repeat\(3,/);
});
