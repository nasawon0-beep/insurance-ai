import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import ts from 'typescript';
const source = name => fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');
const root = new URL('../../', import.meta.url);
const css = source('styles/navy.css');

test('navy shell keeps six existing destinations and mounted panels', () => {
  const app = source('App.tsx');
  for (const [id,label] of [['home','홈'],['customers','고객'],['data','자료분석'],['coverage','보장분석'],['assistant','AI 문의'],['diagnostics','설정·진단']]) {
    assert.ok(app.includes(`tab("${id}", "${label}")`));
    assert.ok(app.includes(`aria-hidden={view !== "${id}"}`));
  }
  assert.match(app, /className="app-sidebar"/);
  assert.match(app, /className="app-navigation" aria-label="주요 메뉴"/);
  assert.match(app, /id="app-content"/);
  assert.match(app, /aria-current=\{view === key/);
  assert.match(css, /position: sticky/);
  assert.match(css, /@media\(max-width:800px\)/);
  assert.match(css, /--color-bg-base: #1b1e20/);
  assert.match(css, /--primary-600: #304f76/);
});

test('API calls and payload expressions stay identical to the approved base', () => {
  const apiNames = new Set(['api','engineFetch','fetch','invoke','savePolicies','saveCustomer','reloadDetail','refreshList']);
  function calls(text) {
    const file = ts.createSourceFile('source.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const result=[];
    function visit(node) {
      if(ts.isCallExpression(node) && apiNames.has(node.expression.getText(file))) result.push(node.getText(file).replace(/\s+/g,' '));
      ts.forEachChild(node, visit);
    }
    visit(file); return result;
  }
  for(const name of ['App.tsx','Customers.tsx','PolicyList.tsx','Assistant.tsx']) {
    const before = execFileSync('git',['show',`HEAD:desktop/src/${name}`],{cwd:root,encoding:'utf8'});
    assert.deepEqual(calls(source(name)),calls(before),name);
  }
});

test('contract actions and popup keyboard access remain reachable', () => {
  const customers=source('Customers.tsx');
  assert.match(customers,/aria-label=\{`\$\{c.name\} 고객 상세 열기`\}/);
  assert.match(customers,/event.key === "Enter" \|\| event.key === " "/);
  assert.match(customers,/className="contract-policy-table"/);
  assert.match(customers,/whiteSpace: "normal" as const, overflow: "visible"/);
  assert.match(source('PolicyList.tsx'),/event.key === "Escape"/);
  assert.match(source('PolicyList.tsx'),/previous\?\.focus\(\)/);
});

test('test fixtures and comparison controls are not shipped', () => {
  for(const name of ['App.tsx','Customers.tsx','PolicyList.tsx','Assistant.tsx']) {
    assert.doesNotMatch(source(name), /ui-fixture|isolated-test-token|가상 건강보장 계약|가상 응답입니다|id="concept"/);
  }
});
