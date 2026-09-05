import assert from "node:assert/strict";
import test from "node:test";

import { policyProgressPct } from "../desktop/src/policyProgress.ts";

const today = new Date(2026, 5, 15);

test("policy progress calculates elapsed percentage", () => {
  assert.equal(policyProgressPct("2025-06-15", "2027-06-15", today), 50);
});

test("policy progress rejects invalid date ranges", () => {
  assert.equal(policyProgressPct("2026-06-15", "2026-06-15", today), null);
  assert.equal(policyProgressPct("2027-06-15", "2026-06-15", today), null);
  assert.equal(policyProgressPct(null, "2027-06-15", today), null);
  assert.equal(policyProgressPct("2025-06-15", null, today), null);
});

test("policy progress clamps dates outside the policy period", () => {
  assert.equal(policyProgressPct("2027-06-15", "2028-06-15", today), 0);
  assert.equal(policyProgressPct("2024-06-15", "2025-06-15", today), 100);
});

test("policy progress rejects nonexistent calendar dates", () => {
  assert.equal(policyProgressPct("2026-02-30", "2027-06-15", today), null);
});

test("policy progress rounds to an integer", () => {
  const pct = policyProgressPct("2026-01-01", "2026-12-31", new Date(2026, 6, 2));
  assert.equal(Number.isInteger(pct), true);
});

test("policy progress accepts a leap day", () => {
  const pct = policyProgressPct("2024-02-29", "2025-02-28", new Date(2024, 6, 1));
  assert.equal(typeof pct, "number");
});

test("policy progress rejects a leap day in a common year", () => {
  assert.equal(policyProgressPct("2025-02-29", "2026-06-15", today), null);
});

test("policy progress rejects an invalid today", () => {
  assert.equal(policyProgressPct("2025-06-15", "2027-06-15", new Date("nope")), null);
});
