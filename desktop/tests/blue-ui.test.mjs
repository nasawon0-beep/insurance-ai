import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = (name) => fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8');
const css = src('styles/blue.css');

// ---- helpers -------------------------------------------------------------------------------
function block(selector) {
  // Returns the declarations text of the first rule whose selector list equals `selector`.
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = css.match(new RegExp('(?:^|[}/])\\s*' + esc + '\\s*\\{([^}]*)\\}'));
  assert.ok(m, `blue.css is missing rule: ${selector}`);
  return m[1];
}
function token(scope, name) {
  const m = block(scope).match(new RegExp('--' + name + '\\s*:\\s*([^;]+);'));
  assert.ok(m, `${scope} is missing --${name}`);
  return m[1].trim().toLowerCase();
}
const lum = (hex) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// ---- 1. approved blue palette (light + dark) --------------------------------------------------
test('light theme uses the approved #0055FF palette and 10px cards', () => {
  assert.equal(token(':root', 'primary-600'), '#0055ff');
  assert.equal(token(':root', 'color-accent-text'), '#0055ff');
  assert.equal(token(':root', 'color-text-primary'), '#08163c');
  assert.equal(token(':root', 'color-border-default'), '#e4e9f2');
  assert.equal(token(':root', 'color-accent-soft'), '#edf4ff');
  assert.equal(token(':root', 'moa-muted'), '#6e7b96');
  assert.equal(token(':root', 'moa-radius'), '10px');
});

test('dark theme has its own blue palette and no navy leftovers', () => {
  assert.equal(token('[data-theme="dark"]', 'color-bg-surface'), '#111e34');
  assert.equal(token('[data-theme="dark"]', 'color-accent-text'), '#6f9eff');
  assert.equal(token('[data-theme="dark"]', 'color-text-primary'), '#e5edff');
  assert.equal(token('[data-theme="dark"]', 'color-border-default'), '#2e3e59');
  assert.doesNotMatch(css, /#304f76|#e7edf4|#1b1e20/i, 'old navy values must not survive in the blue layer');
});

test('text/background pairs meet WCAG AA (4.5:1) in light and dark', () => {
  const pairs = [];
  for (const scope of [':root', '[data-theme="dark"]']) {
    const surface = token(scope, 'color-bg-surface');
    pairs.push([scope + ' primary text', token(scope, 'color-text-primary'), surface]);
    pairs.push([scope + ' secondary text', token(scope, 'color-text-secondary'), surface]);
    pairs.push([scope + ' accent text', token(scope, 'color-accent-text'), surface]);
    pairs.push([scope + ' accent on soft', token(scope, 'color-accent-text'), token(scope, 'color-accent-soft')]);
    pairs.push([scope + ' filled button', '#ffffff', token(scope, 'primary-600')]);
    pairs.push([scope + ' filled button (inline primary-500 fill)', '#ffffff', token(scope, 'primary-500')]);
    pairs.push([scope + ' status chip', token(scope, 'moa-mint-ink'), token(scope, 'moa-mint')]);
  }
  for (const [name, fg, bg] of pairs) {
    assert.ok(ratio(fg, bg) >= 4.5, `${name}: ${fg} on ${bg} = ${ratio(fg, bg).toFixed(2)}`);
  }
});

test('blue layer loads last and the real entry point imports it', () => {
  const main = src('main.tsx');
  const order = ['./styles/index.css', './styles/navy.css', './styles/legacy-surfaces.css', './styles/blue.css'].map((x) => main.indexOf(x));
  assert.ok(order.every((i) => i >= 0), 'main.tsx must import all four style layers');
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'blue.css must be imported after navy/legacy layers');
});

// ---- 2. sidebar -------------------------------------------------------------------------------
test('sidebar keeps six destinations and gains the brand mark plus decorative icons', () => {
  const app = src('App.tsx');
  const brand = src('components/MoaBrand.tsx');
  for (const [id, label] of [['home', '홈'], ['customers', '고객'], ['data', '자료분석'], ['coverage', '보장분석'], ['assistant', 'AI 문의'], ['diagnostics', '설정·진단']]) {
    assert.ok(app.includes(`tab("${id}", "${label}")`), `${label} tab must remain`);
  }
  assert.match(app, /import \{[^}]*MoaLogo[^}]*NavIcon[^}]*\} from "\.\/components\/MoaBrand"/);
  assert.match(app, /<MoaLogo\s*\/>/);
  assert.match(app, /<NavIcon name=/);
  // Decorative: icons and the mark must never be announced twice.
  assert.match(brand, /aria-hidden="true"/);
  assert.match(brand, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(brand, /fetch\(|invoke\(|localStorage/);
});

// ---- 3. data analysis (자료분석) -------------------------------------------------------------
test('data analysis uses accessible text tabs, not demo behaviour', () => {
  const app = src('App.tsx');
  const start = app.indexOf('function DataAnalysisScreen');
  const end = app.indexOf('function DiagnosticsScreen');
  const body = app.slice(start, end);
  assert.match(body, /role="tablist"/);
  assert.match(body, /role="tab"/);
  assert.match(body, /aria-selected=\{section === key\}/);
  assert.match(body, /className="data-analysis"/);
  for (const label of ['새고객 등록', '일괄등록', '고객자료 PDF', '보장분석서 PDF']) assert.ok(body.includes(label), label);
});

test('data analysis body, table and button text is at least 14px', () => {
  for (const sel of ['.data-analysis', '.data-analysis table', '.data-analysis button', '.data-analysis input,\n.data-analysis select,\n.data-analysis textarea']) {
    const decl = block(sel);
    const px = Number((decl.match(/font-size:\s*(\d+(?:\.\d+)?)px/) || [])[1]);
    assert.ok(px >= 14, `${sel} font-size ${px}px`);
  }
});

test('mock-only behaviour from the design drafts never ships', () => {
  for (const name of ['App.tsx', 'Customers.tsx', 'CoverageAnalysis.tsx', 'PolicyList.tsx', 'components/MoaBrand.tsx']) {
    assert.doesNotMatch(src(name), /예시 PDF|검토 도구|AI 조작 중|실제 앱 연결 없음|가상 PDF|데모토글|demo-toggle|process-track|setInterval\([^)]*progress/i, name);
  }
});

// ---- 4. customers / contracts ----------------------------------------------------------------
test('customer rows keep accessible button semantics and add a decorative avatar', () => {
  const customers = src('Customers.tsx');
  assert.match(customers, /aria-label=\{`\$\{c\.name\} 고객 상세 열기`\}/);
  assert.match(customers, /className="customer-row"/);
  assert.match(customers, /className="customer-avatar" aria-hidden="true"/);
  assert.match(customers, /className="customers-detail-tabs"/);
  assert.match(customers, /className="customer-kpi"/);
});

test('list column is 286px and sidebar is slim, per the approved layout', () => {
  assert.match(block('.customers-list-panel'), /width:\s*286px/);
  assert.match(css, /\.app-shell\s*\{[^}]*grid-template-columns:\s*(1[3-6]\d)px/);
});

test('cards, table headers and status chips follow the approved component style', () => {
  assert.match(block('.card,\n.coverage-group-card,\n.coverage-summary-card'), /border-radius:\s*var\(--moa-radius\)/);
  assert.match(css, /\.contract-policy-table th\s*\{[^}]*background:\s*var\(--moa-subtle\)/);
  assert.match(css, /\.badge-success[^{]*\{[^}]*var\(--moa-mint\)/);
});

test('visible keyboard focus is retained', () => {
  assert.match(css, /:focus-visible/);
  assert.doesNotMatch(css, /outline:\s*none(?!\s*!?\s*;?\s*\/\*\s*ok)/);
});
