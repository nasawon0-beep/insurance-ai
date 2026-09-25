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

test("coverage analysis tab uses consultations coverage_json instead of coverage-analysis API", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.doesNotMatch(coverage, /coverage-upload/);
  assert.doesNotMatch(coverage, /type="file"/);
  assert.doesNotMatch(coverage, /PDF 업로드/);
  assert.doesNotMatch(coverage, /\/customers\/\$\{customerId\}\/coverage-analysis/);
  assert.doesNotMatch(coverage, /coverage-analysis\/recalculate/);
  assert.match(coverage, /\/customers\/\$\{customerId\}\/consultations/);
  assert.match(coverage, /coverage_json/);
  assert.match(coverage, /parseCoverageJson/);
});

test("coverage analysis starts blank until a customer is selected", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /const \[customerId, setCustomerId\] = useState\(""\)/);
  assert.doesNotMatch(coverage, /김철수/);
  assert.match(coverage, /placeholder=\{customersBusy \? "고객 불러오는 중…" : "고객을 검색하세요\.\.\."\}/);
});

test("coverage analysis maps coverage_json rows into thirteen tabs", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /const COVERAGE_TABS: CoverageTab\[\] = \[[\s\S]*\{ key: "death", label: "사망" \}[\s\S]*\{ key: "dental", label: "치아\/화상\/골절"/);
  assert.match(coverage, /categoryIdForCoverageName\("질병사망"\)[\s\S]*"death"/);
  assert.match(coverage, /categoryIdForCoverageName\("유사암 진단비"\)[\s\S]*"cancer"/);
  assert.match(coverage, /categoryIdForCoverageName\("뇌혈관 진단비"\)[\s\S]*"brain"/);
  assert.match(coverage, /categoryIdForCoverageName\("급성심근경색 진단비"\)[\s\S]*"heart"/);
  assert.match(coverage, /buildCoverageResponseFromRows/);
});

test("coverage catalog defines the full backend catalog of forty-six items", () => {
  const catalog = read("desktop/src/coverageCatalog.ts");
  const itemCount = (catalog.match(/id: "/g) ?? []).length;
  const categoryCount = new Set([...catalog.matchAll(/category: "([^"]+)"/g)].map((match) => match[1])).size;

  assert.equal(itemCount, 46);
  assert.equal(categoryCount, 13);
  assert.match(catalog, /name: "질병사망", category: "사망", recommended: 100000000/);
  assert.match(catalog, /name: "골절 진단비", category: "치아 \/ 화상 \/ 골절", recommended: 300000/);
});

test("coverage analysis screens expand saved coverage_json rows with uninsured catalog defaults", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");
  const customers = read("desktop/src/Customers.tsx");

  assert.match(coverage, /import \{ COVERAGE_CATALOG/);
  assert.match(coverage, /for \(const catalogItem of COVERAGE_CATALOG\)/);
  assert.match(coverage, /status: matchedRow\?\.status \|\| "미가입"/);
  assert.match(coverage, /current_amount: currentAmount/);
  assert.match(coverage, /gap_amount: Math\.max\(0, catalogItem\.recommended - currentAmount\)/);
  assert.match(coverage, /summary[\s\S]*total_items: COVERAGE_CATALOG\.length/);

  assert.match(customers, /import \{ expandCoverageRowsWithCatalog/);
  assert.match(customers, /expandCoverageRowsWithCatalog\(rows\)/);
});

test("coverage analysis supports inline row editing and saves coverage_json", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /const \[editingRowId, setEditingRowId\] = useState<string \| null>\(null\)/);
  assert.match(coverage, /수정/);
  assert.match(coverage, /저장/);
  assert.match(coverage, /취소/);
  assert.match(coverage, /<select[\s\S]*value=\{editDraft\.status\}/);
  assert.match(coverage, /\/consultations\/\$\{latestConsultation\.id\}/);
  assert.match(coverage, /method: "PATCH"/);
  assert.match(coverage, /coverage_json: JSON\.stringify\(nextRows\)/);
  assert.match(coverage, /await loadAnalysis\(\)/);
});

test("coverage analysis guards empty consultations and corrupt coverage_json without render crashes", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /Array\.isArray\(body\?\.consultations\)/);
  assert.match(coverage, /setLatestConsultation\(null\)/);
  assert.match(coverage, /parsed\.corrupt/);
  assert.match(coverage, /저장된 보장분석 데이터 형식이 손상되었습니다/);
  assert.match(coverage, /로컬 엔진 연결 실패/);
  assert.match(coverage, /HTTP \$\{res\.status\}/);
  assert.doesNotMatch(coverage, /const \[recalculating, setRecalculating\]/);
  assert.doesNotMatch(coverage, /manual_reanalysis/);
  assert.doesNotMatch(coverage, /recalculateAnalysis/);
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

test("coverage analysis accepts nullable API bodies and direct consultation arrays", () => {
  const coverage = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverage, /const list = Array\.isArray\(body\?\.customers\) \? body\.customers : \[\]/);
  assert.match(coverage, /Array\.isArray\(body\) \? body : Array\.isArray\(body\?\.consultations\) \? body\.consultations : \[\]/);
  assert.match(coverage, /typeof consultation\?\.coverage_json === "string"/);
});

test("home drop intake has a top-right save button without removing bottom save", () => {
  const app = read("desktop/src/App.tsx");

  assert.match(app, /aria-label="상단 분석 후 저장"[\s\S]*onClick=\{run\}/);
  assert.match(app, /justifyContent: "space-between"[\s\S]*던져넣기/);
  assert.match(app, /<button onClick=\{run\} disabled=\{busy\} style=\{\{ marginTop: 6, fontWeight: 600 \}\}>/);
});

test("customer policy table uses fixed readable columns and single-line expiry text", () => {
  const customers = read("desktop/src/Customers.tsx");
  const policyTable = customers.slice(customers.indexOf("const policyCellStyle"));

  assert.match(policyTable, /tableLayout:\s*"fixed"[\s\S]*width:\s*"100%"/);
  assert.match(policyTable, /<colgroup>[\s\S]*width:\s*120[\s\S]*width:\s*"auto"[\s\S]*width:\s*130[\s\S]*width:\s*100[\s\S]*width:\s*140[\s\S]*width:\s*90[\s\S]*width:\s*200/);
  assert.match(policyTable, /whiteSpace:\s*"nowrap"[\s\S]*overflow:\s*"hidden"[\s\S]*textOverflow:\s*"ellipsis"/);
  assert.match(policyTable, /borderRight:\s*"1px solid #eee"/);
  assert.match(policyTable, /fontSize:\s*11/);
  assert.match(policyTable, /` \(납입 \$\{p\.payment_end_date\}\)`/);
  assert.doesNotMatch(policyTable, /납입종료 \{p\.payment_end_date\}/);
});

test("customer coverage panel only shows saved coverage_json and no legacy run button", () => {
  const customers = read("desktop/src/Customers.tsx");
  const coveragePanel = customers.slice(
    customers.indexOf("function CoveragePanel"),
    customers.indexOf("function AskPanel"),
  );

  assert.match(coveragePanel, /저장된 보장분석/);
  assert.match(coveragePanel, /보장분석 데이터가 없습니다\. 던져넣기나 고객 등록 시 보장분석 PDF를 업로드하세요\./);
  assert.doesNotMatch(coveragePanel, /분석 실행/);
  assert.doesNotMatch(coveragePanel, /coverage-analysis`/);
  assert.doesNotMatch(coveragePanel, /const \[busy, setBusy\]/);
  assert.doesNotMatch(coveragePanel, /const \[res, setRes\]/);
});
