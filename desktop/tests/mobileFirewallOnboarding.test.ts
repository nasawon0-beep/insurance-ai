import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
const source = readFileSync(new URL('../src/components/MobileConnectionPanel.tsx', import.meta.url), 'utf8');

test('panel separates server, verified firewall settings and unverified phone; guides start/approve/QR', () => {
  for (const text of ['서버 상태', '방화벽 설정', '휴대폰 연결', '현재 접속이나 동기화 완료를 뜻하지 않습니다', '같은 Wi-Fi 또는 핫스팟 연결 → 연결 시작 → QR로 폰 연결', '휴대폰 연결 허용', 'LocalSubnet', '게스트', '회사', '시간 초과']) assert.ok(source.includes(text), text);
  assert.ok(!source.includes('규칙 제거'));
  assert.ok(source.includes('공용 네트워크 허용 해제'));
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
  assert.match(source, /withNetworkNotice\(createQr\)/);
  assert.match(source, /withNetworkNotice\(createCode\)/);
  assert.ok(source.split('if (!qrAllowed) throw new Error').length >= 4, 'native credential paths retain fail-closed gate');
  assert.match(source, /firewallStatus\?\.qa/);
});
