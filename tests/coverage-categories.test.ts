import assert from "node:assert/strict";
import test from "node:test";

import { categoryOf, groupByCategory } from "../desktop/src/coverageCategories.ts";

test("categoryOf maps known names and falls back for unknown names", () => {
  assert.equal(categoryOf("일반암진단비"), "암");
  assert.equal(categoryOf("존재안하는이름"), "기타");
});

test("categoryOf ignores whitespace differences from PDF extraction", () => {
  // _parse_coverage_status 는 원문 PDF 띄어쓰기를 그대로 따라가 "일반암 진단비"처럼
  // 공백이 들어간 이름을 낼 수 있다(tests/test_intake_and_audio.py 실사례).
  assert.equal(categoryOf("일반암 진단비"), "암");
  assert.equal(categoryOf("허혈성심장질환 진단비"), "심장질환");
  assert.equal(categoryOf("  통합암진단비  "), "암");
});

test("groupByCategory follows category order and puts 기타 last", () => {
  const groups = groupByCategory([
    { name: "질병사망" },
    { name: "일반암진단비" },
    { name: "모르는항목" },
  ]);

  assert.deepEqual(groups.map((group) => group.title), ["사망 · 후유장해", "암", "기타"]);
  assert.equal(groups.length, 3);
});

test("groupByCategory returns an empty array for empty input", () => {
  assert.deepEqual(groupByCategory([]), []);
});

test("groupByCategory preserves input order within a category", () => {
  const rows = [
    { name: "특정암진단비", marker: 1 },
    { name: "일반암진단비", marker: 2 },
    { name: "유사암진단비", marker: 3 },
  ];

  assert.deepEqual(groupByCategory(rows)[0].rows.map((row) => row.marker), [1, 2, 3]);
});
