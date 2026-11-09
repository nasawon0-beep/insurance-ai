import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { mobileStatusPollMs, pairingQrCloseReason } from "../src/components/mobileSyncPolicy.ts";

const panel = fs.readFileSync(new URL("../src/components/MobileConnectionPanel.tsx", import.meta.url), "utf8");
const app = fs.readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("startup stuck repro: always-mounted settings screen keeps polling status while visible", () => {
  // 2.5.13 결함: 패널이 마운트 시 1회만 상태를 읽어, 자동 복원 중 starting=true 가 영구 표시됐다.
  assert.equal(mobileStatusPollMs({ starting: true }, true), 1500);
  assert.equal(mobileStatusPollMs({ starting: false }, true), 10000);
  assert.equal(mobileStatusPollMs({ starting: true }, false), null);
  assert.match(app, /<MobileConnectionPanel visible=\{visible\} \/>/);
  assert.match(panel, /setInterval\(\(\) => \{\n?\s*void invoke<MobileSyncStatus>\("mobile_sync_status"\)/);
  assert.match(panel, /if \(!visible\) return;\s*\n\s*void refresh\(\)/);
});

test("simulated timeline: starting snapshot is replaced by running status after poll", async () => {
  // 시작 시각 t0 에 starting, 9초 뒤 서버 준비 → 다음 poll(1.5초 주기) 안에 running 표시.
  const timeline = [
    { at: 0, status: { running: false, starting: true } },
    { at: 9000, status: { running: true, starting: false } },
  ];
  const statusAt = (ms) => [...timeline].reverse().find((entry) => entry.at <= ms).status;
  let shown = statusAt(0);
  for (let ms = 0; ms <= 15000; ) {
    const wait = mobileStatusPollMs(shown, true);
    ms += wait;
    shown = statusAt(ms);
    if (shown.running) {
      assert.ok(ms <= 9000 + 1500, `running shown at ${ms}ms`);
      return;
    }
  }
  assert.fail("status never refreshed to running");
});

test("QR close policy: expiry, stop, address change, successful pairing", () => {
  const qr = { expiresAt: 1000, url: "https://192.168.0.73:18443" };
  const running = { running: true, url: "https://192.168.0.73:18443" };
  assert.equal(pairingQrCloseReason(qr, running, 999, 0, 0), null);
  assert.match(pairingQrCloseReason(qr, running, 1000, 0, 0), /만료/);
  assert.match(pairingQrCloseReason(qr, { running: false, url: qr.url }, 10, 0, 0), /중지/);
  assert.match(pairingQrCloseReason(qr, { running: true, url: "https://172.20.10.2:18443" }, 10, 0, 0), /주소가 바뀌어/);
  assert.match(pairingQrCloseReason(qr, running, 10, 1, 2), /등록되었습니다/);
});

test("QR is rendered locally from the Rust SVG, never via an external service or logs", () => {
  assert.match(panel, /invoke<PairingQr>\("mobile_sync_create_pairing_qr"\)/);
  assert.match(panel, /data:image\/svg\+xml;base64,\$\{btoa\(qr\.svg\)\}/);
  assert.doesNotMatch(panel, /https?:\/\/(?!\/)[^"'`\s]*qr/i);
  assert.doesNotMatch(panel, /console\.(log|info|debug)\([^)]*code/i);
  assert.doesNotMatch(panel, /dangerouslySetInnerHTML/);
});
