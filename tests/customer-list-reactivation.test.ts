import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const baseline = process.env.CUSTOMER_LIST_BASELINE_REF;
const repo = fileURLToPath(new URL('..', import.meta.url));
const readSource = (name: string) => baseline
  ? execFileSync('git', ['-C', repo, 'show', `${baseline}:desktop/src/${name}`], { encoding: 'utf8' })
  : fs.readFileSync(new URL(`../desktop/src/${name}`, import.meta.url), 'utf8');
import ts from '../desktop/node_modules/typescript/lib/typescript.js';

// Execute the actual production callback/effects, not a reimplementation of
// list fetching. Only React scheduling and the authenticated API boundary are synthetic.
const source = readSource('Customers.tsx');
const tree = ts.createSourceFile('Customers.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const component = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'CustomersScreen');
assert.ok(component && ts.isFunctionDeclaration(component) && component.body);
const statements = component.body.statements.filter(n =>
  (ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(tree) === 'refreshList')) ||
  (ts.isExpressionStatement(n) && ts.isCallExpression(n.expression) && n.expression.expression.getText(tree) === 'useEffect' &&
    /refreshList|focusCustomer/.test(n.expression.arguments[0].getText(tree))));
const executable = ts.transpileModule(statements.map(n => n.getText(tree)).join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r)); };
function harness() {
  let slots: any[] = [], cursor = 0, effects: (() => void)[] = [];
  const rows = [{ id: 'old', name: '합성 기존', policy_count: 1 }];
  const requests: string[] = [];
  const c: any = {
    visible: true, focusCustomer: null, debouncedQ: '', sort: 'name', filterExpiring: false,
    filterFollowUp: false, filterTag: '', statusFilter: '', ownOnly: false,
    listAbortRef: { current: null }, listGenRef: { current: 0 }, deletedCustomerIdsRef: { current: new Set() },
    list: [], selected: null, error: null, URLSearchParams, AbortController, Error,
    setList: (v: any) => { c.list = v; }, setAllTags: () => {}, setError: (e: any) => { c.error = e; },
    setSelectedId: (id: string) => { c.selected = id; }, onFocusConsumed: () => {},
    api: async (path: string) => {
      requests.push(path);
      if (path === '/tags') return { tags: [] };
      const q = new URL(path, 'https://fixture.invalid').searchParams.get('q') || '';
      return { customers: rows.filter(r => r.name.includes(q)).map(r => ({ ...r })) };
    },
    useCallback: (fn: any, deps: any[]) => {
      const i = cursor++; const old = slots[i];
      if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) slots[i] = { deps, fn };
      return slots[i].fn;
    },
    useEffect: (fn: any, deps: any[]) => {
      const i = cursor++; const old = slots[i];
      if (!old || deps.some((d, j) => !Object.is(d, old.deps[j]))) { slots[i] = { deps }; effects.push(fn); }
    },
  };
  const context = vm.createContext(c);
  return { c, rows, requests, async render(props = {}) {
    Object.assign(c, props); cursor = 0; effects = [];
    // Block scope permits rerendering declarations while preserving hook slots.
    vm.runInContext(`{${executable}}`, context); effects.forEach(fn => fn()); await flush();
  } };
}
test('returning from PDF save refreshes the mounted list and keeps existing policy count', async () => {
  const h = harness(); await h.render(); await h.render({ visible: false });
  h.rows.push({ id: 'new', name: '합성 신규', policy_count: 1 });
  await h.render({ visible: true, focusCustomer: { id: 'new', nonce: 1 } });
  assert.deepEqual(h.c.list.map((x: any) => x.id), ['old', 'new']);
  assert.equal(h.c.list[0].policy_count, 1); assert.equal(h.c.selected, 'new');
});
test('return from home without a focus request also refreshes', async () => {
  const h = harness(); await h.render(); await h.render({ visible: false });
  h.rows.push({ id: 'new', name: '합성 신규', policy_count: 1 });
  await h.render({ visible: true }); assert.equal(h.c.list.length, 2);
});
test('same visible customer focus refreshes server-derived list fields', async () => {
  const h = harness(); await h.render({ focusCustomer: { id: 'old', nonce: 1 } });
  h.rows[0].policy_count = 2;
  await h.render({ focusCustomer: { id: 'old', nonce: 2 } });
  assert.equal(h.c.list[0].policy_count, 2);
});
test('reactivation preserves all query filters and does not force excluded customer into list', async () => {
  const h = harness(); await h.render({ debouncedQ: '기존', sort: 'updated', filterTag: '합성', statusFilter: '가입', ownOnly: true, filterExpiring: true, filterFollowUp: true });
  await h.render({ visible: false }); h.rows.push({ id: 'new', name: '합성 신규', policy_count: 1 });
  await h.render({ visible: true, focusCustomer: { id: 'new', nonce: 1 } });
  assert.deepEqual(h.c.list.map((x: any) => x.id), ['old']);
  const u = new URL(h.requests.filter(p => p.startsWith('/customers')).at(-1)!, 'https://fixture.invalid');
  assert.equal(u.searchParams.get('q'), '기존'); assert.equal(u.searchParams.get('tag'), '합성');
  assert.equal(u.searchParams.get('own'), '1'); assert.equal(u.searchParams.get('status'), '가입');
  assert.equal(u.searchParams.get('sort'), 'updated'); assert.equal(u.searchParams.get('expiring_days'), '30');
  assert.equal(u.searchParams.get('has_follow_up'), 'true');
});
test('App wires visibility only to persistent customer list', () => {
  const app = readSource('App.tsx');
  assert.ok(/<CustomersScreen\s+screen="list"\s+visible=\{view === "customers"\}/.test(app), 'persistent list receives active-tab visibility');
});

test('late pre-save response cannot replace the reactivated list', async () => {
  const h = harness(); const pending: ((value: any) => void)[] = [];
  h.c.api = (path: string) => path === '/tags' ? Promise.resolve({ tags: [] }) : new Promise(resolve => pending.push(resolve));
  await h.render(); await h.render({ visible: false });
  await h.render({ visible: true, focusCustomer: { id: 'new', nonce: 1 } });
  assert.ok(pending.length >= 2);
  pending.at(-1)!({ customers: [{ id: 'new', name: '합성 신규' }] }); await flush();
  pending.slice(0, -1).forEach(resolve => resolve({ customers: [{ id: 'old', name: '합성 기존' }] }));
  await flush(); assert.deepEqual(h.c.list.map((x: any) => x.id), ['new']);
});
