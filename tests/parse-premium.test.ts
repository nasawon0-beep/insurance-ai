import assert from "node:assert/strict";
import test from "node:test";

import { parsePremium } from "../desktop/src/parsePremium.ts";

test("empty / whitespace becomes null", () => {
  assert.equal(parsePremium(""), null);
  assert.equal(parsePremium("   "), null);
});

test("plain digits parse", () => {
  assert.equal(parsePremium("100000"), 100000);
  assert.equal(parsePremium("0"), 0);
});

test("comma / space / currency marks are stripped", () => {
  assert.equal(parsePremium("100,000"), 100000);
  assert.equal(parsePremium(" 1,234,567 "), 1234567);
  assert.equal(parsePremium("₩50,000"), 50000);
  assert.equal(parsePremium("50000원"), 50000);
});

test("non-numeric input throws instead of silently becoming null", () => {
  assert.throws(() => parsePremium("abc"), /숫자로 입력/);
  assert.throws(() => parsePremium("10만원"), /숫자로 입력/);
});

test("negative premium is rejected", () => {
  assert.throws(() => parsePremium("-1000"), /숫자로 입력/);
});
