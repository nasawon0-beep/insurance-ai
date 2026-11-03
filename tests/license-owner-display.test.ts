// 소유자 화면: 권한(사용 가능)과 일반 이용권 기록을 분리 — 지난 무료체험 종료일이 사용 종료처럼 보이지 않아야 한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { licenseRows, licenseNotice, type LicenseState } from "../desktop/src/licenseStatus.ts";

const past = "2026-10-01T03:00:00.000Z";
const base: LicenseState = {
  plan: "trial", device_limit: 0, expiry: past, status: "expired", days_left: 0,
  starts_at: "2026-09-24T03:00:00.000Z", ends_at: past, trial_started_at: "2026-09-24T03:00:00.000Z",
  kind: "trial", remaining_seconds: 0,
};

test("owner with past trial: usable, no 종료/남은 기간/플랜 rows, trial shown only as record", () => {
  const rows = licenseRows({ ...base, role: "owner", effective_status: "active" });
  const m = Object.fromEntries(rows);
  assert.equal(m["역할"], "소유자");
  assert.match(m["사용 상태"], /^사용 가능 ✅ — 소유자 권한\(이용권 기간과 무관\)$/);
  assert.equal(m["일반 이용권 기록"], "무료체험 · 기간 종료(2026-10-01 12:00) — 소유자 사용에는 영향 없음");
  for (const k of ["종료", "남은 기간", "플랜", "상태", "이용 형태"]) assert.ok(!(k in m), k);
  assert.ok(!rows.some(([, v]) => /trial|TRIAL|만료|❌/.test(v)));
  assert.equal(licenseNotice({ ...base, role: "owner", effective_status: "active" }, false), null);
});

test("owner offline and owner without any subscription", () => {
  const off = Object.fromEntries(licenseRows({ ...base, role: "owner", effective_status: "active", offline: true }));
  assert.match(off["사용 상태"], /\(오프라인 확인\)$/);
  const none = licenseRows({ ...base, kind: null, status: "none", ends_at: null, expiry: null, role: "owner", effective_status: "active" });
  assert.deepEqual(none.map(([k]) => k), ["역할", "사용 상태"]);
});

test("member rows unchanged: expired trial still shows 만료 and 종료", () => {
  const m = Object.fromEntries(licenseRows({ ...base, role: null, effective_status: "expired" }));
  assert.equal(m["상태"], "무료체험 만료 ❌");
  assert.equal(m["종료"], "2026-10-01 12:00");
  assert.equal(m["이용 형태"], "무료체험");
  assert.match(m["시작"], /이메일 확인 완료/);
});

test("member lifetime shows 없음(무기한)", () => {
  const m = Object.fromEntries(licenseRows({ ...base, kind: "lifetime", status: "active", ends_at: null, expiry: null, remaining_seconds: null, role: null, effective_status: "active" }));
  assert.equal(m["종료"], "없음(무기한)");
  assert.equal(m["상태"], "무기한 무료 · 사용 중 ✅");
});
