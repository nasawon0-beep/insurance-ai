import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const read = (path: string) => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("Windows window preserves HTML5 file drop transport", () => {
  const rust = read("desktop/src-tauri/src/lib.rs");
  assert.match(rust, /let builder = builder\.disable_drag_drop_handler\(\);/);
  assert.doesNotMatch(rust, /builder\.drag_and_drop\(false\)/);
});

test("all four data-analysis inputs retain DOM drop handlers and shared downstream paths", () => {
  const app = read("desktop/src/App.tsx");
  const customers = read("desktop/src/Customers.tsx");
  const importer = read("desktop/src/ImportWizard.tsx");

  assert.match(customers, /onDrop=\{\(e\)[\s\S]{0,300}setStagedIntake\(f\)/);
  assert.match(customers, /const pickIntakeFile[\s\S]{0,500}setStagedIntake\(selected\)/);
  assert.match(importer, /onDrop=\{(?:async )?\(e\)[\s\S]{0,350}loadFile\(f\)/);
  assert.match(importer, /const pick[\s\S]{0,500}loadFile\(f\)/);
  assert.match(app, /onDrop=\{\(e\)[\s\S]{0,300}setStaged\(f\)/);
  assert.match(app, /const pickFile[\s\S]{0,700}setStaged\(f\)/);
  assert.match(app, /<CapturePanel[\s\S]{0,100}title="고객자료 PDF"/);
  assert.match(app, /<CapturePanel[\s\S]{0,100}title="보장분석서 PDF"/);
});

test("policy save skips summary pseudo-policies and reports nested-save partial state", () => {
  const policyList = read("desktop/src/PolicyList.tsx");
  assert.match(policyList, /if \(isAggregateSummaryPolicy\(p\)\) continue/);
  assert.ok(policyList.indexOf("if (createdPolicy) saved++") > policyList.indexOf("/policy-coverages/bulk"));
  assert.match(policyList, /describePolicySaveFailure\(e, createdPolicy, savingCoverages\)/);
});

test("visible desktop branding is 모아둠 while technical identity remains stable", () => {
  const app = read("desktop/src/App.tsx");
  const login = read("desktop/src/LoginScreen.tsx");
  const onboarding = read("desktop/src/components/OnboardingScreen.tsx");
  const html = read("desktop/index.html");
  const rust = read("desktop/src-tauri/src/lib.rs");
  const tauriConfig = read("desktop/src-tauri/tauri.conf.json");
  const engine = read("desktop/src/engine.ts");

  assert.match(app, /모아둠 시작 중/);
  assert.match(app, /className="app-brand">모아<span>둠<\/span>/);
  assert.match(login, />\s*모아둠\s*</);
  assert.match(onboarding, /모아둠을 사용할 준비가 되었습니다/);
  assert.match(onboarding, /모아둠 초기 설정/);
  assert.match(html, /<title>모아둠<\/title>/);
  assert.match(rust, /\.title\("모아둠"\)/);

  assert.match(tauriConfig, /"productName": "Insurance AI"/);
  assert.match(tauriConfig, /"mainBinaryName": "Insurance AI"/);
  assert.match(tauriConfig, /"identifier": "com\.abc\.insurance-ai"/);
  assert.match(rust, /\.join\("Insurance AI"\)/);
  assert.match(engine, /X-Insurance-AI-Secret/);
});