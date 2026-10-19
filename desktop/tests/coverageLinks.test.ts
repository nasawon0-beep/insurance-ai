import assert from "node:assert/strict";
import { resolveMatchedPolicyCoverages } from "../src/coverageLinks.ts";

const rows = new Map([
  ["coverage-death", { id: "coverage-death", name: "질병사망", amount: 11_000_000 }],
  ["coverage-cancer", { id: "coverage-cancer", name: "일반암 진단비", amount: 30_000_000 }],
]);

const currentAmount = 11_000_000;
const exact = resolveMatchedPolicyCoverages(
  {
    matched_policy_ids: ["contract-1"],
    matched_policy_coverage_ids: ["coverage-death"],
  },
  rows,
);
assert.deepEqual(exact.map((row) => row.id), ["coverage-death"]);
assert.equal(exact[0]?.amount, currentAmount);
assert.equal(currentAmount, 11_000_000);

const emptyWins = resolveMatchedPolicyCoverages(
  {
    matched_policy_ids: ["coverage-cancer"],
    matched_policy_coverage_ids: [],
  },
  rows,
);
assert.deepEqual(emptyWins, []);

const missingCoverageIds = resolveMatchedPolicyCoverages(
  { matched_policy_ids: ["coverage-cancer"] },
  rows,
);
assert.deepEqual(missingCoverageIds, []);

console.log("coverageLinks regression PASS");
