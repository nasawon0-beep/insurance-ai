import assert from "node:assert/strict";
import test from "node:test";

import { parseCoverageJson } from "../desktop/src/coverageRows.ts";

test("null / empty json is 'no content', not corrupt", () => {
  assert.deepEqual(parseCoverageJson(null), { rows: [], corrupt: false });
  assert.deepEqual(parseCoverageJson(""), { rows: [], corrupt: false });
});

test("valid json parses to rows", () => {
  const json = JSON.stringify([
    { name: "일반암진단비", status: "부족", pct: 40, current: "2천만원", recommended: "5천만원" },
  ]);
  const { rows, corrupt } = parseCoverageJson(json);
  assert.equal(corrupt, false);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "일반암진단비");
});

test("broken json is flagged corrupt (distinct from empty)", () => {
  assert.deepEqual(parseCoverageJson("{not json"), { rows: [], corrupt: true });
});

test("valid json that is not an array is corrupt, not empty", () => {
  assert.deepEqual(parseCoverageJson('{"x":1}'), { rows: [], corrupt: true });
  assert.deepEqual(parseCoverageJson("42"), { rows: [], corrupt: true });
  assert.deepEqual(parseCoverageJson("null"), { rows: [], corrupt: true });
});

test("empty array is valid and not corrupt", () => {
  assert.deepEqual(parseCoverageJson("[]"), { rows: [], corrupt: false });
});
