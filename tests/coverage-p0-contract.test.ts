import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { coverageAmountLabel } from "../desktop/src/coverageAmount.ts";

test("unknown, explicit zero, and known coverage amounts have distinct UI labels", () => {
  assert.equal(coverageAmountLabel(null, "unknown_dash", "-"), "금액 확인필요");
  assert.equal(coverageAmountLabel(null, "unknown_blank", ""), "금액 확인필요");
  assert.equal(coverageAmountLabel(0, "explicit_zero", "0원"), "0원 (원문 명시)");
  assert.equal(coverageAmountLabel(7_000_000, "known", "700만"), "7,000,000원");
});

test("customer detail and independent screens use the same dedicated snapshot component and API", () => {
  const customers = fs.readFileSync(new URL("../desktop/src/Customers.tsx", import.meta.url), "utf8");
  const independent = fs.readFileSync(new URL("../desktop/src/CoverageAnalysis.tsx", import.meta.url), "utf8");
  for (const source of [customers, independent]) {
    assert.match(source, /CoverageSourceSnapshot/);
    assert.match(source, /\/coverage-analysis\?audience=internal/);
    assert.match(source, /\/policy-coverages/);
  }
  assert.match(customers, /await loadDedicatedAnalysis\(\)/);
  assert.match(customers, /refreshed\.run_id !== result\?\.run_id/);
});

test("shared snapshot tracks IDs without visible internal IDs and separates unlinked from review-required ledger", () => {
  const source = fs.readFileSync(new URL("../desktop/src/CoverageSourceSnapshot.tsx", import.meta.url), "utf8");
  assert.match(source, /data-run-id/);
  assert.match(source, /data-item-id/);
  assert.match(source, /data-detail-id/);
  assert.match(source, /표준담보 미연결 원장/);
  assert.match(source, /검수 필요 원장/);
  assert.match(source, /미연결 여부와 독립적으로/);
  assert.match(source, /연결 여부와 검수 필요 여부는 별도입니다/);
  assert.match(source, /standard_name_extracted_raw/);
  assert.match(source, /standard_name_verification/);
  assert.match(source, /검증 원문/);
  assert.match(source, /셀 추출 원문/);
  assert.match(source, /신정원 원본분류 검수 필요/);
  assert.match(source, /원본 상세연결 검수/);
  assert.match(source, /occurrence\.source_page/);
  assert.match(source, /occurrence\.resolution_reason/);
  assert.match(source, /선택 연결/);
  assert.match(source, /보류/);
  assert.match(source, /candidate_policy_coverage_ids/);
  assert.match(source, /candidateIds\.has\(row\.id\)/);
  assert.match(source, /candidateRows\.map/);
  assert.match(source, /연결 후보가 없어 보류만 가능합니다/);
  assert.match(source, /held_source_link_occurrences/);
  assert.match(source, /보류\/미확정/);
  assert.match(source, /보류 사유/);
  assert.doesNotMatch(source, />\s*\{(?:analysis\.run_id|item\.id|detail\.id)\}\s*</);
});
