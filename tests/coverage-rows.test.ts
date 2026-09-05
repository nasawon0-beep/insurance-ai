import assert from "node:assert/strict";
import test from "node:test";

import { normalizeCoverageRows, type CoverageRow } from "../desktop/src/coverageRows.ts";

test("empty rows are dropped while rows with coverage values remain", () => {
  const rows = [
    { name: "", status: "미가입", pct: 0, current: null, recommended: null },
    { name: "", status: "미가입", pct: 0, current: "가입", recommended: null },
  ];
  assert.deepEqual(normalizeCoverageRows(rows), [rows[1]]);
  assert.deepEqual(normalizeCoverageRows([]), []);
});

test("pct values are converted and clamped", () => {
  const values = ["80", Number.NaN, 150, -5];
  const rows = values.map((pct, i) => ({ name: String(i), status: "충분", pct, current: null, recommended: null }));
  assert.deepEqual(normalizeCoverageRows(rows as CoverageRow[]).map((row) => row.pct), [80, 0, 100, 0]);
});

test("coverage strings and statuses are normalized", () => {
  const statuses = ["", " 부족 ", "충분", "기타"];
  const rows = statuses.map((status, i) => ({
    name: String(i), status, pct: 0,
    current: i === 0 ? "  " : i === 1 ? " 2천만원 " : null,
    recommended: null,
  }));
  const normalized = normalizeCoverageRows(rows);
  assert.deepEqual(normalized.map((row) => row.status), ["미가입", "부족", "충분", "기타"]);
  assert.deepEqual(normalized.map((row) => row.current), [null, "2천만원", null, null]);
});

test("input order is preserved", () => {
  const rows: CoverageRow[] = [
    { name: "충분 행", status: "충분", pct: 100, current: null, recommended: null },
    { name: "미가입 행", status: "미가입", pct: 0, current: null, recommended: null },
  ];
  assert.deepEqual(normalizeCoverageRows(rows).map((row) => row.name), ["충분 행", "미가입 행"]);
});

test("malformed rows are dropped and valid rows are normalized", () => {
  const rows = [{}, null, "x", 3, { name: "정상", status: "부족", pct: "50", current: " 1천 ", recommended: null }];
  assert.deepEqual(normalizeCoverageRows(rows), [
    { name: "정상", status: "부족", pct: 50, current: "1천", recommended: null },
  ]);
});

test("whitespace-only empty rows are dropped after normalization", () => {
  const rows = [{ name: "   ", status: "", pct: 999, current: "  ", recommended: null }];
  assert.deepEqual(normalizeCoverageRows(rows), []);
});

test("object rows without fields do not throw", () => {
  assert.doesNotThrow(() => normalizeCoverageRows([{}]));
});
