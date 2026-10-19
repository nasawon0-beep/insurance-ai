import assert from "node:assert/strict";
import fs from "node:fs";

const app = fs.readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const customers = fs.readFileSync(new URL("../src/Customers.tsx", import.meta.url), "utf8");
const policyList = fs.readFileSync(new URL("../src/PolicyList.tsx", import.meta.url), "utf8");

assert.match(app, /selectReviewedPolicyCoverages\(row\)/);
assert.match(app, /skipped_existing/);
assert.match(app, /observed_summary/);
assert.match(app, /요약금액으로 계약담보를 생성하지 않습니다/);
assert.doesNotMatch(app, /\[통합 보장분석 저장\] 실패[\s\S]{0,120}catch/);
assert.match(customers, /saveCoverageLedgerFor\(created\.id/);
assert.match(customers, /saveCoverageLedgerFor\(selectedId/);
assert.match(customers, /saveCoverageLedgerFor\(targetId/);
assert.match(customers, /saveExtractedAnalysis\(ex\.id, row\)/);
assert.match(customers, /saveExtractedAnalysis\(created\.id, row\)/);
assert.match(customers, /보장분석 연결·갱신/);
assert.match(customers, /coverage-analysis\/link-refresh/);
assert.match(customers, /source_kind === "observed_summary"/);
assert.match(policyList, /existingPolicies\.find/);
assert.match(policyList, /skipped_existing/);
console.log("coverage general flow source regression PASS");
