import assert from 'node:assert/strict';
import { test } from 'node:test';
const policy = await import('../src/components/mobileFirewallPolicy.ts');
import type { MobileFirewallStatus } from '../src/components/mobileFirewallPolicy.ts';
const ready: MobileFirewallStatus = {
  supported: true, qa: false, networkCategory: 'private', ruleReady: true,
  ruleState: 'ready', canApply: true, programPath: 'C:/fixture/local-engine.exe',
  ruleName: 'fixture-only', connectivityVerified: false, lastError: null, applyOutcome: 'applied',
};

test('firewall polling is single-flight, times out closed, ignores late results and disposed updates', async () => {
  assert.equal(typeof policy.createFirewallPoller, 'function');
  let finish!: (value: MobileFirewallStatus) => void;
  let calls = 0;
  const results: MobileFirewallStatus[] = [];
  const errors: string[] = [];
  const poller = policy.createFirewallPoller(() => { calls++; return new Promise(resolve => { finish = resolve; }); }, value => results.push(value), error => errors.push(error), 10);
  poller.refresh(); poller.refresh();
  assert.equal(calls, 1);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(errors.length, 1);
  poller.refresh();
  assert.equal(calls, 1, 'timed-out native query must not spawn overlapping processes');
  finish(ready);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(results.length, 0, 'late success cannot reopen QR');
  poller.refresh();
  assert.equal(calls, 2);
  poller.dispose();
  finish(ready);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(results.length, 0);
});

test('network invalidation discards an in-flight old snapshot and queues one fresh query', async () => {
  let finish!: (value: MobileFirewallStatus) => void;
  let calls = 0;
  const results: MobileFirewallStatus[] = [];
  const poller = policy.createFirewallPoller(() => { calls++; return new Promise(resolve => { finish = resolve; }); }, value => results.push(value), () => {}, 1000);
  poller.refresh();
  poller.refresh(true);
  poller.refresh(true);
  finish(ready);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(results.length, 0);
  assert.equal(calls, 2);
  finish({ ...ready, networkCategory: 'public', ruleReady: false });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(results[0].networkCategory, 'public');
  poller.dispose();
});

test('resolved rejection is failure even when an old rule is ready; disable must be verified', () => {
  assert.equal(typeof policy.firewallApplyError, 'function');

  assert.equal(policy.firewallApplyError(ready, true), null);
  for (const applyOutcome of [null, 'failed', 'blocked', 'qaRejected', 'notConfirmed'] as const) {
    assert.ok(policy.firewallApplyError({ ...ready, applyOutcome }, true));
  }
  assert.ok(policy.firewallApplyError({ ...ready, ruleReady: false }, true));
  assert.ok(policy.firewallApplyError({ ...ready, lastError: 'denied' }, true));
  assert.ok(policy.firewallApplyError(ready, false));
  assert.equal(policy.firewallApplyError({ ...ready, ruleReady: false, ruleState: 'disabled' }, false), null);
  assert.equal(policy.firewallApplyError({ ...ready, ruleReady: false, ruleState: 'missing' }, false), null);
});

test('QR requires running server and verified Private rule; QA fixtures can exercise the read-only path', () => {
  assert.equal(typeof policy.firewallQrAllowed, 'function', 'firewall QR policy must exist');

  assert.equal(policy.firewallQrAllowed(true, ready), true);
  assert.equal(policy.firewallQrAllowed(false, ready), false);
  assert.equal(policy.firewallQrAllowed(true, null), false);
  for (const networkCategory of ['public', 'domain', 'unavailable'] as const) {
    assert.equal(policy.firewallQrAllowed(true, { ...ready, networkCategory }), false);
  }
  assert.equal(policy.firewallQrAllowed(true, { ...ready, ruleReady: false }), false);
  assert.equal(policy.firewallQrAllowed(true, { ...ready, supported: false }), false);
  assert.equal(policy.firewallQrAllowed(true, { ...ready, lastError: 'query failed' }), false);
  assert.equal(policy.firewallQrAllowed(true, { ...ready, qa: true }), true);
});
test('Public requires explicit warning consent AND verified OS rule; neither bypasses errors or QA', () => {
  const status={...ready,networkCategory:'public' as const};
  assert.equal(policy.firewallQrAllowed(true,status),false);
  assert.equal(policy.firewallQrAllowed(true,status,true),true);
  assert.equal(policy.firewallQrAllowed(true,{...status,ruleReady:false},true),false);
  assert.equal(policy.firewallQrAllowed(true,{...status,lastError:'query failed'},true),false);
  assert.equal(policy.firewallQrAllowed(true,{...status,qa:true},true),false);
  assert.equal(policy.firewallQrAllowed(true,{...status,networkCategory:'unavailable'},true),false);
});
