import assert from "node:assert/strict";
import test from "node:test";

import { daysUntil, expiringCount, isLumpSum, isYearly, totalMonthlyPremium } from "../desktop/src/customerKpi.ts";

test("isYearly recognizes yearly cycles", () => {
  for (const cycle of ["YEARLY", "연납", "년납", "yearly"]) assert.equal(isYearly(cycle), true);
  for (const cycle of ["MONTHLY", null, "", "매월납"]) assert.equal(isYearly(cycle), false);
  assert.equal(isYearly("일시납"), false);
});

test("isLumpSum recognizes lump-sum cycles", () => {
  assert.equal(isLumpSum("일시납"), true);
  assert.equal(isLumpSum("LUMP_SUM"), true);
  assert.equal(isLumpSum("MONTHLY"), false);
  assert.equal(isLumpSum(null), false);
});

test("totalMonthlyPremium applies monthly, yearly, status, and invalid-premium rules", () => {
  assert.deepEqual(totalMonthlyPremium([
    { premium: 10000, payment_cycle: "MONTHLY", status: "ACTIVE" },
    { premium: 20000, payment_cycle: "MONTHLY", status: "ACTIVE" },
    { premium: 30000, payment_cycle: "MONTHLY", status: "ACTIVE" },
  ]), { total: 60000, lumpSumCount: 0 });
  assert.deepEqual(totalMonthlyPremium([
    { premium: 120000, payment_cycle: "YEARLY", status: "ACTIVE" },
    { premium: 100000, payment_cycle: "YEARLY", status: "ACTIVE" },
  ]), { total: 18333, lumpSumCount: 0 });
  assert.deepEqual(totalMonthlyPremium([
    { premium: 10000, payment_cycle: "MONTHLY", status: "LAPSED" },
    { premium: 10000, payment_cycle: "MONTHLY", status: "EXPIRED" },
    { premium: 10000, payment_cycle: "MONTHLY", status: "CANCELLED" },
    { premium: 7000, payment_cycle: null, status: "ACTIVE" },
    { premium: null, payment_cycle: "MONTHLY", status: "ACTIVE" },
    { premium: 0, payment_cycle: "MONTHLY", status: "ACTIVE" },
  ]), { total: 7000, lumpSumCount: 0 });
  assert.deepEqual(totalMonthlyPremium([
    { premium: 500000, payment_cycle: "일시납", status: "ACTIVE" },
  ]), { total: 0, lumpSumCount: 1 });
  // 백엔드 assistant._monthly_premium 과 동일 결과
  assert.deepEqual(totalMonthlyPremium([
    { premium: 45000, payment_cycle: "MONTHLY", status: "ACTIVE" },
    { premium: 120000, payment_cycle: "YEARLY", status: "ACTIVE" },
    { premium: 5000000, payment_cycle: "일시납", status: "ACTIVE" },
    { premium: 99999, payment_cycle: "MONTHLY", status: "LAPSED" },
  ]), { total: 55000, lumpSumCount: 1 });
  assert.deepEqual(totalMonthlyPremium([]), { total: 0, lumpSumCount: 0 });
});

test("daysUntil calculates day boundaries and ignores invalid dates", () => {
  const today = new Date(2026, 8, 4, 15, 30);
  assert.equal(daysUntil("2026-09-04", today), 0);
  assert.equal(daysUntil("2026-10-04", today), 30);
  assert.equal(daysUntil("2026-09-03", today), -1);
  assert.equal(daysUntil("not-a-date", today), null);
  assert.equal(daysUntil("2026-02-30", today), null);
  assert.equal(daysUntil("2026-04-31", today), null);
});

test("expiringCount includes only active policies within inclusive boundaries", () => {
  const today = new Date(2026, 8, 4);
  assert.equal(expiringCount([
    { end_date: "2026-09-04", status: "ACTIVE" },
    { end_date: "2026-10-04", status: "ACTIVE" },
    { end_date: "2026-10-05", status: "ACTIVE" },
    { end_date: "2026-09-03", status: "ACTIVE" },
    { end_date: "2026-09-05", status: "LAPSED" },
    { end_date: null, status: "ACTIVE" },
    { end_date: "2026-13-99", status: "ACTIVE" },
  ], 30, today), 2);
  assert.equal(expiringCount([
    { end_date: "2026-09-04", status: "ACTIVE" },
    { end_date: "2026-09-05", status: "ACTIVE" },
  ], 0, today), 1);
});

test("expiringCount default today argument smoke test", () => {
  assert.equal(typeof expiringCount([], 30), "number");
});
