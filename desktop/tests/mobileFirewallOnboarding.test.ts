import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
const source = readFileSync(new URL('../src/components/MobileConnectionPanel.tsx', import.meta.url), 'utf8');

test('panel separates server, verified firewall settings and unverified phone; guides start/approve/QR', () => {
  for (const text of ['서버 상태', '방화벽 설정', '휴대폰 연결', '아직 확인되지 않음', '1. 연결 시작', '2. 방화벽 승인', '3. QR', 'LocalSubnet', '게스트', '회사', '시간 초과']) assert.ok(source.includes(text), text);
  assert.ok(!source.includes('규칙 제거'));
  assert.ok(source.includes('규칙 비활성화'));
});

test('read-only firewall polling is independent, refreshed on network changes, and cleaned up', () => {
  assert.match(source, /createFirewallPoller\(/);
  assert.match(source, /"mobile_sync_firewall_status"/);
  assert.match(source, /addEventListener\("online"/);
  assert.match(source, /addEventListener\("focus"/);
  assert.match(source, /poller\.dispose\(\)/);
  const refresh = source.slice(source.indexOf('const refresh ='), source.indexOf('// 앱 시작'));
  assert.ok(!refresh.includes('mobile_sync_firewall_status'), 'server refresh cannot await PowerShell');
});

test('explicit consent and structured apply outcome precede success; all new pairing paths gated', () => {
  const action = source.slice(source.indexOf('const firewall ='), source.indexOf('const row ='));
  assert.match(action, /window\.confirm\(message\)/);
  assert.match(action, /confirmed: true/);
  assert.match(action, /firewallApplyError\(result, enable\)/);
  assert.ok(action.indexOf('throw new Error') < action.indexOf('setNotice'));
  assert.match(source, /disabled=\{busy \|\| !qrAllowed\} onClick=\{\(\) => void createQr/);
  assert.match(source, /disabled=\{busy \|\| !qrAllowed\} onClick=\{\(\) => void createCode/);
  assert.match(source, /firewallStatus\?\.qa/);
});
