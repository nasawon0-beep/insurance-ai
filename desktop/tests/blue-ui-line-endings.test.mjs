import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

// Exercise the actual CSS assertions in isolated source copies, never rewrite the checkout.
const files = ['styles/blue.css', 'main.tsx', 'App.tsx', 'Customers.tsx',
  'CoverageAnalysis.tsx', 'PolicyList.tsx', 'components/MoaBrand.tsx'];
function runAssertions(eol, mutate = (css) => css) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'moadoom-blue-eol-'));
  try {
    for (const name of files) {
      let content = fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
      if (name === 'styles/blue.css') content = mutate(content);
      const dest = path.join(root, 'src', name);
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.writeFileSync(dest, content.replace(/\n/g, eol));
    }
    const testPath = path.join(root, 'tests/blue-ui.test.mjs');
    fs.mkdirSync(path.dirname(testPath), { recursive: true });
    fs.copyFileSync(new URL('./blue-ui.test.mjs', import.meta.url), testPath);
    // A fresh runner must not inherit the parent's internal child-test context.
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const result = spawnSync(process.execPath, ['--test', '--test-reporter=tap', testPath], { env, encoding: 'utf8', timeout: 30000 });
    assert.ifError(result.error);
    return { status: result.status, output: result.stdout + result.stderr };
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

for (const [name, eol] of [['LF', '\n'], ['CRLF', '\r\n']]) {
  test(`blue CSS semantic assertions pass with ${name} checkout`, () => {
    const result = runAssertions(eol);
    assert.equal(result.status, 0, result.output);
    assert.match(result.output, /# pass 12\b/);
    assert.match(result.output, /# skipped 0\b/);
  });
  for (const [fault, pattern, replacement, diagnostic] of [
    ['13px form text', /(\.data-analysis input,\n\.data-analysis select,\n\.data-analysis textarea\s*\{[^}]*font-size:\s*)14px/, '$113px', /font-size 13px/],
    ['wrong card radius', /(\.card,\n\.coverage-group-card,\n\.coverage-summary-card\s*\{[^}]*border-radius:\s*)var\(--moa-radius\)/, '$120px', /border-radius/],
    ['missing selector', /\.data-analysis input,\n\.data-analysis select,\n\.data-analysis textarea/, '.unrelated-selector', /blue.css is missing rule: .data-analysis input/],
  ]) {
    test(`blue CSS assertions reject ${fault} with ${name}`, () => {
      const result = runAssertions(eol, (css) => {
        const changed = css.replace(pattern, replacement);
        assert.notEqual(changed, css, 'negative control must actually change its target rule');
        return changed;
      });
      assert.equal(result.status, 1, result.output);
      assert.match(result.output, diagnostic);
      assert.match(result.output, /# fail 1\b/, 'only the deliberately broken assertion must fail');
    });
  }
}
