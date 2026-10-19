import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("..", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");

test("light mode tokens use stronger borders, varied backgrounds, hover, and coverage emphasis fills", () => {
  const tokens = read("desktop/src/styles/tokens.css");
  const cardCss = read("desktop/src/styles/card.css");
  const coverageCss = read("desktop/src/styles/coverage.css");

  assert.match(tokens, /--color-border-default:\s*#d4d4d8/i);
  assert.match(tokens, /--color-bg-base:\s*#fafaf9/i);
  assert.match(tokens, /--color-bg-surface:\s*#f4f4f5/i);
  assert.match(tokens, /--color-bg-hover:\s*#e4e4e7/i);
  assert.match(tokens, /--coverage-sufficient-bg:\s*#dbeafe/i);
  assert.match(tokens, /--coverage-shortage-bg:\s*#fee2e2/i);
  assert.match(tokens, /--coverage-uninsured-bg:\s*#f3f4f6/i);
  assert.match(tokens, /--coverage-priority-critical-bg:\s*#fed7aa/i);
  assert.match(cardCss, /\.card[\s\S]*box-shadow:\s*var\(--shadow-md\)/);
  assert.match(cardCss, /\.card-hover:hover[\s\S]*box-shadow:\s*var\(--shadow-lg\)/);
  assert.match(coverageCss, /\.coverage-badge--sufficient\s*\{[^}]*background:\s*var\(--coverage-sufficient-bg\)/);
  assert.match(coverageCss, /\.coverage-badge--shortage\s*\{[^}]*background:\s*var\(--coverage-shortage-bg\)/);
  assert.match(coverageCss, /\.coverage-badge--uninsured\s*\{[^}]*background:\s*var\(--coverage-uninsured-bg\)/);
  assert.match(coverageCss, /\.coverage-badge--unknown\s*\{[^}]*background:\s*var\(--coverage-priority-critical-bg\)/);
});

test("coverage-analysis PDFs saved from drop intake trigger automatic recalculation and allow same-file reselect", () => {
  const app = read("desktop/src/App.tsx");
  const customers = read("desktop/src/Customers.tsx");

  for (const source of [app, customers]) {
    assert.match(source, /보장분석[\s\S]*coverage-analysis\/recalculate/);
    assert.match(source, /coverage-analysis\/recalculate/);
  }

  assert.match(customers, /input\.value\s*=\s*["']["']/);
  assert.match(app, /input\.value\s*=\s*["']["']/);
});

test("coverage analysis tab uses dedicated analysis and policy-ledger APIs", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const refresh = read("desktop/src/coverageRefresh.ts");

  assert.doesNotMatch(coverage, /coverage-upload/);
  assert.doesNotMatch(coverage, /type="file"/);
  assert.doesNotMatch(coverage, /PDF 업로드/);
  assert.match(coverage, /\/coverage-analysis-runs/);
  assert.match(coverage, /\/customers\/\$\{customerId\}\/coverage-analysis\?audience=internal/);
  assert.match(coverage, /\/customers\/\$\{customerId\}\/policy-coverages/);
  assert.match(coverage, /refreshLatestCoverageAnalysis/);
  assert.match(refresh, /coverage-analysis\/link-refresh/);
  assert.doesNotMatch(coverage, /\/customers\/\$\{customerId\}\/consultations/);
});

test("coverage analysis starts blank until a customer is selected", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /const \[customerId, setCustomerId\] = useState\(""\)/);
  assert.doesNotMatch(coverage, /김철수/);
  assert.match(coverage, /placeholder=\{customersBusy \? "고객 불러오는 중…" : "고객을 검색하세요\.\.\."\}/);
});

test("coverage analysis maps backend categories into the ordered category tab set", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const snapshot = read("desktop/src/CoverageSourceSnapshot.tsx");

  assert.match(coverage, /CoverageSourceSnapshot/);
  assert.match(snapshot, /COVERAGE_CATEGORY_TABS = \[[\s\S]*"전체"[\s\S]*"사망·후유장해"[\s\S]*"기타"/);
  assert.match(snapshot, /COVERAGE_CATEGORY_TABS\.map\(\(category\)/);
  assert.match(snapshot, /if \(activeTab === "전체"\) return categories/);
  assert.match(snapshot, /coverageCategoryTab\(category\.category_group\) === activeTab/);
});

test("coverage catalog defines the full backend catalog of fifty items", () => {
  const catalog = read("desktop/src/coverageCatalog.ts");
  const itemCount = (catalog.match(/id: "/g) ?? []).length;
  const categoryCount = new Set([...catalog.matchAll(/category: "([^"]+)"/g)].map((match) => match[1])).size;

  assert.equal(itemCount, 50);
  assert.equal(categoryCount, 13);
  assert.match(catalog, /name: "질병사망", category: "사망", recommended: 100000000/);
  assert.match(catalog, /name: "통합암 진단비", category: "암"/);
  assert.match(catalog, /name: "특정암 진단비", category: "암"/);
  assert.match(catalog, /name: "뇌산정특례대상 진단비", category: "뇌혈관질환"/);
  assert.match(catalog, /name: "심장산정특례대상 진단비", category: "심장질환"/);
  assert.match(catalog, /name: "골절 진단비", category: "치아 \/ 화상 \/ 골절", recommended: 300000/);
});

test("coverage analysis joins standard analysis items to original policy ledger rows", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const snapshot = read("desktop/src/CoverageSourceSnapshot.tsx");

  assert.match(coverage, /analysis=\{data\}/);
  assert.match(coverage, /policyCoverages=\{policyCoverages\}/);
  assert.match(snapshot, /matched_policy_coverage_ids/);
  assert.match(snapshot, /new Map\(policyCoverages\.map\(\(row\) => \[row\.id, row\]\)\)/);
  assert.match(snapshot, /detailIds\.map/);
  assert.match(snapshot, /detail\.insurer/);
  assert.match(snapshot, /detail\.product_name/);
  assert.match(snapshot, /detail\.rider_name_raw \|\| detail\.rider_name/);
});

test("coverage analysis tab renders category-standard-contract tree and summary", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const snapshot = read("desktop/src/CoverageSourceSnapshot.tsx");

  assert.match(coverage, /CoverageSourceSnapshot/);
  assert.match(snapshot, /visibleCategories\.map/);
  assert.match(snapshot, /category\.items\.map/);
  assert.match(snapshot, /<CoverageDetail/);
  assert.match(snapshot, /<CoverageBadge status=\{status\}/);
  assert.match(snapshot, /원본 요약 없음 · 계약담보 자체계산/);
  assert.match(snapshot, /원본과 다름/);
  assert.doesNotMatch(snapshot, /충분|부족/);
});

test("coverage analysis refresh reloads and compare-and-swaps the latest observed revision", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const refresh = read("desktop/src/coverageRefresh.ts");

  assert.match(coverage, /refreshLatestCoverageAnalysis/);
  assert.match(refresh, /coverage-analysis\?audience=internal/);
  assert.match(refresh, /coverage-analysis\/link-refresh/);
  assert.match(refresh, /method: "POST"/);
  assert.match(refresh, /run_id: latest\.run_id/);
  assert.doesNotMatch(coverage, /run_id: data\?\.run_id/);
  assert.match(coverage, /docType === "proposal"/);
  assert.match(coverage, /가입제안서는 신규 제안 검토용이라 보유담보에 저장하지 않았습니다/);
  assert.match(coverage, /await loadAnalysis\(\)/);
  assert.doesNotMatch(coverage, /coverage_json: JSON\.stringify/);
});

test("coverage analysis guards missing selections and malformed policy arrays", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /if \(!customerId\) \{[\s\S]*setData\(null\);[\s\S]*setPolicyCoverages\(\[\]\)/);
  assert.match(coverage, /Array\.isArray\(coverages\.policy_coverages\) \? coverages\.policy_coverages : \[\]/);
  assert.match(coverage, /setData\(null\);[\s\S]*setPolicyCoverages\(\[\]\);[\s\S]*setError/);
  assert.match(coverage, /\(data\?\.categories \?\? \[\]\)/);
});

test("customer coverage edit preserves saved row order", () => {
  const policyList = read("desktop/src/PolicyList.tsx");

  assert.match(policyList, /setDraft\(rows\.map\(\(row\) => \(\{ \.\.\.row \}\)\)\)/);
  assert.doesNotMatch(policyList, /setDraft\(sorted\.map/);
});

test("customer coverage edit calculates pct from current and recommended values", () => {
  const policyList = read("desktop/src/PolicyList.tsx");

  assert.match(policyList, /function parseCoverageAmount/);
  assert.match(policyList, /Math\.round\(\(current \/ recommended\) \* 100\)/);
  assert.match(policyList, /recommended > 0 \? Math\.round/);
  assert.match(policyList, /recalculateCoveragePct\(next\)/);
});

test("customer coverage edit renders pct as read-only text instead of an input", () => {
  const policyList = read("desktop/src/PolicyList.tsx");
  const editable = policyList.slice(policyList.indexOf("export function CoverageTableEditable"));

  assert.match(editable, /\{row\.pct\}%/);
  assert.doesNotMatch(editable, /value=\{row\.pct\}/);
  assert.doesNotMatch(editable, /update\(i, \{ pct:/);
});

test("coverage run selector accepts nullable and wrapped API bodies while preferring customer IDs", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /if \(!source\) return \[\]/);
  assert.match(coverage, /Array\.isArray\(source\.runs\)/);
  assert.match(coverage, /Array\.isArray\(source\.items\)/);
  assert.match(coverage, /Array\.isArray\(source\.data\)/);
  assert.match(coverage, /run\.customer_id \?\? customer\?\.customer_id \?\? customer\?\.id \?\? run\.id/);
});

test("home drop intake keeps an explicit user-triggered save action", () => {
  const app = read("desktop/src/App.tsx");

  assert.match(app, /aria-label="고객 등록 및 저장"[\s\S]*onClick=\{run\}[\s\S]*disabled=\{busy\}/);
  assert.match(app, /const saveButtonLabel = busy \? "⏳ 저장 중…" : "💾 고객 등록 및 저장"/);
  assert.match(app, /\{saveButtonLabel\}/);
  assert.match(app, /const run = async \(\) =>/);
});

test("new customer quick registration analysis buttons remain visible buttons", () => {
  const customers = read("desktop/src/Customers.tsx");
  const quick = customers.slice(customers.indexOf("빠른 등록"));

  assert.match(customers, /quickRegistrationPrimaryButtonStyle/);
  assert.match(customers, /quickRegistrationSecondaryButtonStyle/);
  assert.match(quick, /onClick=\{parseIntake\}[\s\S]*style=\{quickRegistrationPrimaryButtonStyle\}[\s\S]*"분석"/);
  assert.match(quick, /onClick=\{pickIntakeFile\}[\s\S]*style=\{quickRegistrationSecondaryButtonStyle\}[\s\S]*파일 선택 \(녹취·PDF·텍스트\)/);
  assert.match(quick, /onClick=\{\(\) => analyzeFile\(stagedIntake\)\}[\s\S]*이 파일 분석/);
});

test("customer policy table uses fixed readable columns and single-line expiry text", () => {
  const customers = read("desktop/src/Customers.tsx");
  const policyTable = customers.slice(customers.indexOf("const policyCellStyle"));

  assert.match(policyTable, /tableLayout:\s*"fixed"[\s\S]*width:\s*"100%"/);
  assert.match(policyTable, /<colgroup>[\s\S]*width:\s*130[\s\S]*minWidth:\s*140[\s\S]*width:\s*110[\s\S]*width:\s*90[\s\S]*width:\s*140[\s\S]*width:\s*80[\s\S]*width:\s*180/);
  assert.match(policyTable, /whiteSpace:\s*"nowrap"[\s\S]*overflow:\s*"hidden"[\s\S]*textOverflow:\s*"ellipsis"/);
  assert.match(policyTable, /borderRight:\s*"1px solid var\(--color-border-default\)"/);
  assert.match(policyTable, /fontSize:\s*11/);
  assert.match(policyTable, /` \(납입 \$\{p\.payment_end_date\}\)`/);
  assert.doesNotMatch(policyTable, /납입종료 \{p\.payment_end_date\}/);
});

test("customer coverage panel uses the same dedicated analysis snapshot and reloads the refreshed run", () => {
  const customers = read("desktop/src/Customers.tsx");
  const coveragePanel = customers.slice(
    customers.indexOf("function CoveragePanel"),
    customers.indexOf("function AskPanel"),
  );

  assert.match(coveragePanel, /coverage-analysis\?audience=internal/);
  assert.match(coveragePanel, /\/policy-coverages/);
  assert.match(coveragePanel, /CoverageSourceSnapshot/);
  assert.match(coveragePanel, /await loadDedicatedAnalysis\(\)/);
  assert.match(coveragePanel, /refreshed\.run_id !== result\?\.run_id/);
  assert.doesNotMatch(coveragePanel, /coverage_json/);
  assert.doesNotMatch(coveragePanel, /CoverageTableEditableSafe/);
});
