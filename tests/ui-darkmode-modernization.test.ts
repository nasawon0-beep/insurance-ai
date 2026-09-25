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
  assert.match(tokens, /--color-text-primary:\s*#fafafa/i);

  const globalInputCss = `${read("desktop/src/styles/input.css")}\n${read("desktop/src/App.css")}`;
  assert.match(globalInputCss, /input,\s*textarea,\s*select[\s\S]*background(?:-color)?:\s*var\(--color-bg-surface\)/);
  assert.doesNotMatch(globalInputCss, /background(?:Color)?:\s*["']#fff(?:fff)?["']/i);
});

test("coverage analysis uses horizontal tabbar, compact table, status icons, and consultations source", () => {
  const coverageCss = read("desktop/src/styles/coverage.css");
  const coverageTsx = read("desktop/src/CoverageAnalysis.tsx");

  assert.match(coverageCss, /\.coverage-tabs[\s\S]*overflow-x:\s*auto/);
  assert.match(coverageCss, /\.coverage-table[\s\S]*min-width:\s*920px/);
  assert.match(coverageCss, /\.coverage-table th,\s*\n\.coverage-table td[\s\S]*padding:\s*var\(--space-2\)/);
  assert.match(coverageCss, /--sufficient[\s\S]*var\(--coverage-sufficient-bg\)/);

  assert.match(coverageTsx, /const COVERAGE_TABS[\s\S]*death[\s\S]*dental/);
  assert.match(coverageTsx, /statusIcon\(item\.status/);
  assert.doesNotMatch(coverageTsx, /type="file"/);
  assert.match(coverageTsx, /\/customers\/\$\{customerId\}\/consultations/);
  assert.match(coverageTsx, /고객 목록에서 수정하세요/);
  assert.doesNotMatch(coverageTsx, /coverage-analysis\/recalculate/);
});

test("requested screens avoid hardcoded white backgrounds and borderless action buttons", () => {
  for (const path of ["desktop/src/App.tsx", "desktop/src/Customers.tsx", "desktop/src/Assistant.tsx"]) {
    const source = read(path);
    assert.doesNotMatch(source, /background(?:Color)?:\s*["']#(?:fff|f5f5f5|eff6ff|fef2f2)/i, path);
    assert.doesNotMatch(source, /border:\s*(?:[^\n?]+\?\s*)?["']none["']/i, path);
  }
});
