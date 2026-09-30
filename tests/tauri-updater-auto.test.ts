import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("..", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");

test("Tauri updater is configured for the GitHub Pages update manifest", () => {
  const cargo = read("desktop/src-tauri/Cargo.toml");
  const config = JSON.parse(read("desktop/src-tauri/tauri.conf.json"));

  assert.match(cargo, /tauri-plugin-updater\s*=\s*"2/);
  assert.deepEqual(config.plugins?.updater?.endpoints, [
    "https://nasawon0-beep.github.io/insurance-ai-updates/api/windows-latest.json",
  ]);
  assert.ok(config.plugins?.updater?.pubkey?.length > 50); // 서명 키 있음
  assert.equal(config.bundle?.createUpdaterArtifacts, true);
});

test("desktop startup schedules one updater check after three seconds and shows install dialog", () => {
  const app = read("desktop/src/App.tsx");
  const rust = read("desktop/src-tauri/src/lib.rs");

  assert.match(app, /setTimeout\([\s\S]*3000\)/);
  assert.match(app, /showUpdateDialog\s*&&\s*updateAvailable/);
  assert.match(app, /download-progress/);
  assert.match(app, /installUpdate\(updateAvailable\.update/);
  assert.match(app, /clearTimeout\(timer\)/);

  assert.match(rust, /tauri_plugin_updater::Builder::new\(\)\.build\(\)/);
});

test("onboarding completion reconnects local-engine without manual app restart", () => {
  const app = read("desktop/src/App.tsx");
  const rust = read("desktop/src-tauri/src/lib.rs");

  assert.match(app, /finishOnboardingAndConnectEngine/);
  assert.match(app, /엔진 연결 중/);
  assert.match(app, /invoke\("ensure_local_engine"\)/);
  assert.match(app, /engineFetch\("\/health"\)/);
  assert.match(app, /앱 재시작/);

  assert.match(rust, /ensure_local_engine/);
  assert.match(rust, /tauri::generate_handler!\[[\s\S]*ensure_local_engine/);
  assert.match(rust, /local_engine[\s\S]*\.lock\(\)/);
});

test("local-engine auth falls back to Tauri secret bridge when /api-secret fetch fails", () => {
  const engine = read("desktop/src/engine.ts");
  const rust = read("desktop/src-tauri/src/lib.rs");

  assert.match(engine, /fetch\(`\$\{LOCAL_ENGINE_URL\}\/api-secret`\)/);
  assert.match(engine, /invoke<string>\("local_engine_api_secret"\)/);
  assert.match(engine, /secretPromise\s*=\s*null/);

  assert.match(rust, /INSURANCE_AI_API_SECRET_FILE/);
  assert.match(rust, /local-engine-api\.secret/);
  assert.match(rust, /fn local_engine_api_secret/);
  assert.match(rust, /tauri::generate_handler!\[[\s\S]*local_engine_api_secret/);
});

test("license diagnostics distinguish online refresh failures and offline grace rejection", () => {
  const auth = read("desktop/src/auth.ts");

  assert.match(auth, /control-server network request failed/);
  assert.match(auth, /license refresh failed; trying cached offline license/);
  assert.match(auth, /offline license unavailable: no cached signed license/);
  assert.match(auth, /offline license unavailable: grace check failed/);
  assert.doesNotMatch(auth, /console\.warn\([\s\S]{0,240}getToken\(\)/);
});

test("offline license banner does not block local-engine analysis screens", () => {
  const app = read("desktop/src/App.tsx");
  const customers = read("desktop/src/Customers.tsx");

  assert.match(app, /오프라인 모드 — 서버에 연결되면 라이선스가 갱신됩니다/);
  assert.match(app, /view === "home" && <HomeScreen/);
  assert.match(app, /view === "newcustomer" && \([\s\S]*<CustomersScreen/);
  assert.doesNotMatch(app, /offline[\s\S]{0,120}disabled=/);
  assert.doesNotMatch(app, /licenseErr[\s\S]{0,120}disabled=/);

  assert.match(app, /api\("\/capture", \{ method: "POST", body: fd \}\)/);
  assert.match(customers, /api\("\/customers\/intake\/bulk", \{/);
  assert.doesNotMatch(customers, /offline[\s\S]{0,120}parseIntake/);
  assert.doesNotMatch(customers, /licenseErr[\s\S]{0,120}parseIntake/);
});
