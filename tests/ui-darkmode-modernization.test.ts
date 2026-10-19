import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("..", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");

test("dark mode tokens expose semantic coverage colors and no white form surfaces", () => {
  const tokens = read("desktop/src/styles/tokens.css");
  assert.match(tokens, /--coverage-sufficient:\s*#3b82f6/i);
  assert.match(tokens, /--coverage-shortage:\s*#ef4444/i);
  assert.match(tokens, /--coverage-uninsured:\s*#6b7280/i);
  assert.match(tokens, /--coverage-priority-critical:\s*#f97316/i);
  assert.match(tokens, /\[data-theme="dark"\][\s\S]*--color-text-primary:\s*#f4f4f5/i);

  const globalInputCss = `${read("desktop/src/styles/input.css")}\n${read("desktop/src/App.css")}`;
  assert.match(globalInputCss, /input,\s*textarea,\s*select[\s\S]*background(?:-color)?:\s*var\(--color-bg-surface\)/);
  assert.doesNotMatch(globalInputCss, /background(?:Color)?:\s*["']#fff(?:fff)?["']/i);
});

test("coverage analysis uses shared horizontal tabs, hierarchical details, and dedicated coverage sources", () => {
  const coverageCss = read("desktop/src/styles/coverage.css");
  const coverageTsx = read("desktop/src/CoverageAnalysis.tsx");
  const coverageRefresh = read("desktop/src/coverageRefresh.ts");
  const snapshotTsx = read("desktop/src/CoverageSourceSnapshot.tsx");

  assert.match(coverageCss, /\.coverage-category-tabs[\s\S]*overflow-x:\s*auto/);
  assert.match(coverageCss, /\.coverage-user-item[\s\S]*border/);
  assert.match(snapshotTsx, /COVERAGE_CATEGORY_TABS\.map\(\(category\)/);
  assert.match(snapshotTsx, /filterCoverageCategories\(analysis\.categories, activeTab\)/);
  assert.match(snapshotTsx, /<CoverageDetail[\s\S]*detail=\{detail\}/);
  assert.match(coverageTsx, /CoverageSourceSnapshot/);
  assert.doesNotMatch(coverageTsx, /type="file"/);
  assert.match(coverageTsx, /\/customers\/\$\{customerId\}\/coverage-analysis\?audience=internal/);
  assert.match(coverageTsx, /\/customers\/\$\{customerId\}\/policy-coverages/);
  assert.match(coverageTsx, /고객 목록에서 수정하세요/);
  assert.match(coverageTsx, /refreshLatestCoverageAnalysis/);
  assert.match(coverageRefresh, /coverage-analysis\/link-refresh/);
});

test("requested screens avoid hardcoded white backgrounds and borderless action buttons", () => {
  for (const path of ["desktop/src/App.tsx", "desktop/src/Customers.tsx", "desktop/src/Assistant.tsx"]) {
    const source = read(path);
    assert.doesNotMatch(source, /background(?:Color)?:\s*["']#(?:fff|f5f5f5|eff6ff|fef2f2)(?![0-9a-f])/i, path);
    assert.doesNotMatch(source, /border:\s*(?:[^\n?]+\?\s*)?["']none["']/i, path);
  }
});
