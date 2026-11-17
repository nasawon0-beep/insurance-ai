import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../desktop/package.json', import.meta.url));
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

test('capture preview never claims manual review is completed, including zero coverage', () => {
  const source = readFileSync(new URL('../desktop/src/App.tsx', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let banner: any;
  function walk(node: any) {
    if (ts.isJsxElement(node) && node.getText(ast).includes('{coverageCount}') && node.children.some((child: any) => ts.isJsxText(child) && child.text.includes('— 고객 '))) banner = node;
    ts.forEachChild(node, walk);
  }
  walk(ast);
  assert.ok(banner, 'actual capture count banner must exist');
  const code = ts.transpileModule(`return (${banner.getText(ast)});`, { compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 } }).outputText;
  for (const coverageCount of [0, 2]) {
    const element = new Function('React', 'activeRows', 'contractCount', 'coverageCount', code)(React, [{}], 1, coverageCount);
    const html = renderToStaticMarkup(element);
    assert.match(html, /분석 미리보기/);
    assert.doesNotMatch(html, /검수 완료|✅/);
    assert.ok(html.includes(`담보 ${coverageCount}건`));
  }
});

test('recognized empty coverage preview saves customer without forcing a ledger recalculation', async () => {
  const source = readFileSync(new URL('../desktop/src/App.tsx', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let initializer: any;
  function walk(node: any) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'recalculateCoverageIfNeeded') initializer = node.initializer;
    ts.forEachChild(node, walk);
  }
  walk(ast); assert.ok(initializer);
  const code = ts.transpileModule(`return (${initializer.getText(ast)});`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  let calls = 0;
  const noop = () => {};
  const bindings = { isProposalDocument: () => false, isCoverageAnalysisDocument: () => true, console: { log: noop, error: noop }, setPhase: noop, setMsg: noop, onBackgroundJob: noop, window: { setTimeout: (f: () => void) => setTimeout(f, 1) }, api: async () => { calls++; return {}; } };
  const recalculate = new Function(...Object.keys(bindings), code)(...Object.values(bindings));
  const result = await recalculate('synthetic-customer', { doc_type: 'coverage_analysis_short', policy_coverages: [], coverage_status: [], coverage_review: { coverages: [] } }, 0);
  assert.equal(result, false);
  assert.equal(calls, 0);
});
