import assert from "node:assert/strict";
import {
  describePolicySaveFailure,
  isAggregateSummaryPolicy,
  isCoverageAnalysisDocument,
  isProposalDocument,
  normalizeCoverageDocumentType,
  selectReviewedPolicyCoverages,
} from "../src/coverageDocument.ts";

for (const value of ["coverage_analysis", "coverage_analysis_full", "coverage_analysis_detail_only", "coverage_analysis_short", "보장분석"]) {
  assert.equal(normalizeCoverageDocumentType(value), "coverage_analysis");
  assert.equal(isCoverageAnalysisDocument(value), true);
}
for (const value of ["proposal", "가입제안서", "가입설계서", "상품제안서"]) {
  assert.equal(isProposalDocument(value), true);
  assert.equal(isCoverageAnalysisDocument(value), false);
}
const direct = [{ insurer: "상세보험", rider_name: "직접담보" }];
const reviewed = [{ insurer: "다른보험", rider_name: "검수담보" }];
assert.deepEqual(selectReviewedPolicyCoverages({ coverage_review: { coverages: reviewed }, policy_coverages: direct }), direct);
assert.deepEqual(selectReviewedPolicyCoverages({ coverage_review: { coverages: reviewed } }), reviewed);
assert.deepEqual(selectReviewedPolicyCoverages({
  policy_coverages: [
    { insurer: "통합보장분석", rider_name: "질병사망", amount: 10_000_000 },
    { insurer: "알 수 없음", rider_name: "상해사망", amount: 20_000_000 },
    { insurer: "원장보험", rider_name: "뇌혈관질환진단비", amount: 30_000_000 },
  ],
}), [{ insurer: "원장보험", rider_name: "뇌혈관질환진단비", amount: 30_000_000 }]);
assert.deepEqual(selectReviewedPolicyCoverages({ policy_coverages: [] }), []);
assert.equal(isAggregateSummaryPolicy({ insurer: "통합보장분석", product_name: "M·보장분석" }), true);
assert.equal(isAggregateSummaryPolicy({ insurer: "알 수 없음", product_name: "보장분석 요약" }), true);
assert.equal(isAggregateSummaryPolicy({ insurer: "정상보험", product_name: "건강보험" }), false);
assert.match(describePolicySaveFailure(new Error("원장 거부"), true, true), /계약은 생성됐지만 담보 저장 실패/);
assert.equal(describePolicySaveFailure(new Error("계약 거부"), false, false), "계약 거부");
console.log("coverageDocument regression PASS");
