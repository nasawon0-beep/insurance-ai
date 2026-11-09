import test from 'node:test';
import assert from 'node:assert/strict';
import * as policy from '../src/components/mobileSyncPolicy.ts';
import fs from 'node:fs';

test('technical information and revoked devices are in collapsed sections', () => {
  const panel = fs.readFileSync(new URL('../src/components/MobileConnectionPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /<summary>연결 문제 해결 · 상세 정보<\/summary>/);
  const details = panel.slice(panel.indexOf('<summary>연결 문제 해결 · 상세 정보'), panel.indexOf('{qr &&'));
  for (const label of ['PC 주소', '인증서 지문', '인증서 만료', '네트워크 종류', '규칙 상태']) assert.ok(details.includes(label), label);
  assert.doesNotMatch(panel, /<details[^>]*\bopen[=>\s]/);
  assert.match(panel, /<summary>해제된 휴대폰/);
  assert.doesNotMatch(panel, /아직 확인되지 않음 — 서버 실행/);
});

test('public network guidance is conditional, offers safe choices and never changes Windows settings', () => {
  const panel = fs.readFileSync(new URL('../src/components/MobileConnectionPanel.tsx', import.meta.url), 'utf8');
  for (const text of ['핫스팟 연결 방법', '집·개인 Wi-Fi를 사용 중이에요', '나중에 연결', '네트워크 다시 확인', '카페·공공 Wi-Fi라면']) assert.ok(panel.includes(text), text);
  assert.match(panel, /networkCategory === "public"/);
  assert.doesNotMatch(panel, /Set-NetConnectionProfile|netsh|shell\.open/);
});

test('device display replaces irrecoverable encoding without modifying the stored name', () => {
  assert.equal(typeof policy.mobileDeviceDisplayName, 'function');
  for (const name of ['\ufffd\ufffd phone', '????', 'Phone\u0081', '   ']) {
    const device = { name };
    assert.equal(policy.mobileDeviceDisplayName(device.name), 'Android 휴대폰');
    assert.equal(device.name, name);
  }
  for (const name of ['상원님 Galaxy', 'Galaxy S24', '내 휴대폰 📱']) {
    assert.equal(policy.mobileDeviceDisplayName(name), name);
  }
});
