import assert from "node:assert/strict";
import test from "node:test";

import { normalizeDate } from "../desktop/src/normalizeDate.ts";

for (const raw of ["20060816", "2006-0816", "2006.8.16", "2006/8/16", "2006 8 16", "2006-8-16"]) {
  test(`normalizes ${raw}`, () => assert.equal(normalizeDate(raw), "2006-08-16"));
}

test("applies two-digit year rules", () => {
  assert.equal(normalizeDate("600606", { birth: true }), "1960-06-06");
  assert.equal(normalizeDate("200606", { birth: true }), "2020-06-06");
  assert.equal(normalizeDate("600606"), "2060-06-06");
});

test("preserves empty input", () => assert.equal(normalizeDate(""), ""));

for (const raw of ["2006-13-40", "abc", "2006-02-30", "20230229"]) {
  test(`rejects ${raw}`, () => assert.equal(normalizeDate(raw), null));
}

test("accepts leap day", () => assert.equal(normalizeDate("20240229"), "2024-02-29"));
