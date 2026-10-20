import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("..", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");

test("customer policy product name toggles the existing full coverage ledger", () => {
  const customers = read("desktop/src/Customers.tsx");
  assert.match(customers, /onClick=\{\(\) => togglePolicyCoverages\(p\)\}/);
  assert.match(customers, /담보 보기 \(\{p\.coverage_count/);
  assert.match(customers, /담보보험료/);
  assert.match(customers, /납입기간/);
  assert.match(customers, /갱신/);
  assert.match(customers, /document_status/);
});

test("proposal save persists a non-confirmed contract ledger without recalculation", () => {
  const app = read("desktop/src/App.tsx");
  const policyList = read("desktop/src/PolicyList.tsx");
  assert.match(app, /row\.policies\?\.length/);
  assert.doesNotMatch(app, /row\.policies\?\.length && !isProposalReview/);
  assert.match(policyList, /document_status/);
  assert.match(policyList, /source_hash/);
});

test("non-confirmed documents require row selection with source coordinates", () => {
  const customers = read("desktop/src/Customers.tsx");
  assert.match(customers, /confirmedCoverageIds/);
  assert.match(customers, /confirmed_coverages/);
  assert.match(customers, /source_page: coverage\.source_page/);
  assert.match(customers, /source_table: coverage\.source_table/);
  assert.match(customers, /source_row: coverage\.source_row/);
  assert.match(customers, /설명·예시·선택특약이 섞일 수 있으며 자동으로 가입담보가 아닙니다/);
  assert.doesNotMatch(customers, /가입이 명시적으로 확정된 계약입니까\?/);
});

test("mixed row status remains split and editable after confirmed policy reopen", () => {
  const customers = read("desktop/src/Customers.tsx");
  assert.match(customers, /confirmedCoverages/);
  assert.match(customers, /referenceCoverages/);
  assert.match(customers, /가입 담보 원장 \{confirmedCoverages\.length\}건/);
  assert.match(customers, /문서 참고행 원장 \{referenceCoverages\.length\}건/);
  assert.match(customers, /coverage_selection_mode/);
  assert.match(customers, /선택을 모두 취소하려면 문서 확정성을 참고\/제안\/확인대기로 변경/);
});

test("successful whole cancellation clears local row selection after the PATCH", () => {
  const customers = read("desktop/src/Customers.tsx");
  assert.match(customers, /await api\(`\/policies\/\$\{editId\}`/);
  assert.match(customers, /editForm\.document_status === "CONFIRMED" \? selectedIds : \[\]/);
  assert.match(customers, /setConfirmedCoverageIds\(\(current\) => \(\{/);
});
