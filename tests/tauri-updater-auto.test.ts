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

  assert.match(engine, /fetchWithTimeout\(`\$\{LOCAL_ENGINE_URL\}\/api-secret`\)/);
  assert.match(engine, /invoke<string>\("local_engine_api_secret"\)/);
  assert.match(engine, /secretPromise\s*=\s*null/);

  assert.match(rust, /INSURANCE_AI_API_SECRET_FILE/);
  assert.match(rust, /local-engine-api\.secret/);
  assert.match(rust, /fn local_engine_api_secret/);
  assert.match(rust, /tauri::generate_handler!\[[\s\S]*local_engine_api_secret/);
});

test("local-engine requests fall back to native bridge without breaking uploads", () => {
  const engine = read("desktop/src/engine.ts");
  const rust = read("desktop/src-tauri/src/lib.rs");
  const cargo = read("desktop/src-tauri/Cargo.toml");

  assert.match(engine, /browserThenNativeEngineFetch/);
  assert.match(engine, /invoke<NativeEngineResponse>\("local_engine_request"/);
  assert.match(engine, /body instanceof FormData[\s\S]*type: "formData"/);
  assert.match(engine, /headers\.delete\("Content-Type"\)/);
  assert.match(engine, /new Response\(new Uint8Array\(result\.body\)/);
  assert.match(engine, /LONG_RUNNING_PATH_PREFIXES[\s\S]*"\/capture"[\s\S]*"\/parse\/pdf"[\s\S]*"\/import\/preview"[\s\S]*"\/import\/commit"/);
  assert.doesNotMatch(engine, /ensureLocalEngineRecovered\(\)[\s\S]{0,160}catch/);

  assert.match(rust, /enum LocalEngineRequestBody[\s\S]*FormData/);
  assert.match(rust, /reqwest::multipart::Form/);
  assert.match(rust, /\.bytes\(\)[\s\S]*bytes\.to_vec\(\)/);
  assert.match(rust, /tauri::generate_handler!\[[\s\S]*local_engine_request/);
  assert.match(cargo, /reqwest = \{ version = "0\.11", features = \["stream", "multipart"\] \}/);
});

test("local-engine startup race waits for health and retries safe initial GET requests only", () => {
  const engine = read("desktop/src/engine.ts");

  assert.match(engine, /STARTUP_RECOVERY_RETRY_DELAYS_MS\s*=\s*\[250, 500, 1000, 1500, 2500\]/);
  assert.match(engine, /waitForLocalEngineReady/);
  assert.match(engine, /browserThenNativeEngineFetch\("\/health", \{\}\)/);
  assert.match(engine, /canRetryEngineStartupRequest[\s\S]*method === "GET" \|\| method === "HEAD"/);
  assert.match(engine, /!isLongRunningEnginePath\(path\)/);
  assert.match(engine, /getApiSecretAfterStartupWait/);
  assert.match(engine, /local-engine request failed before startup completed; waiting for readiness and retrying/);

  const retryBlock = engine.slice(engine.indexOf("export async function engineFetch"));
  assert.match(retryBlock, /await waitForLocalEngineReady\(\);[\s\S]*return engineFetchOnce\(path, init, retryWithFreshSecret\)/);
  assert.doesNotMatch(engine, /ensureLocalEngineRecovered\(\)[\s\S]{0,240}engineFetch/);
});

test("diagnostics initial load shows preparing state and clears stale local-engine errors on success", () => {
  const app = read("desktop/src/App.tsx");
  const diagnostics = app.slice(app.indexOf("function DiagnosticsScreen"), app.indexOf("// 아키텍처 사이드바"));

  assert.match(diagnostics, /const \[enginePreparing, setEnginePreparing\] = useState\(false\)/);
  assert.match(diagnostics, /setEnginePreparing\(true\);[\s\S]*setErr\(null\);[\s\S]*engineFetch\("\/diagnostics"\)/);
  assert.match(diagnostics, /setD\(await \(await engineFetch\("\/diagnostics"\)\)\.json\(\)\);[\s\S]*setErr\(null\);/);
  assert.match(diagnostics, /finally \{[\s\S]*setEnginePreparing\(false\);[\s\S]*\}/);
  assert.match(diagnostics, /로컬 엔진 준비 중… 진단 정보를 곧 불러옵니다/);
  assert.match(diagnostics, /err && !enginePreparing/);
});

test("dashboard startup race retries before showing a permanent error and clears stale errors on success", () => {
  const app = read("desktop/src/App.tsx");

  assert.match(app, /DASHBOARD_STARTUP_RETRY_DELAYS_MS\s*=\s*\[500, 1000, 1500, 2500\]/);
  assert.match(app, /hasDashboardDataRef\s*=\s*useRef\(false\)/);
  assert.match(app, /const retryDelays = hasDashboardDataRef\.current \? \[\] : DASHBOARD_STARTUP_RETRY_DELAYS_MS/);
  assert.match(app, /if \(!hasDashboardDataRef\.current\) setError\(null\)/);
  assert.match(app, /await wait\(retryDelays\[attempt\]\)/);
  assert.match(app, /hasDashboardDataRef\.current = true;[\s\S]*setData\(next\);[\s\S]*setError\(null\);/);
  assert.match(app, /if \(isCurrent\(\)\) setError\(lastError instanceof Error \? lastError\.message : String\(lastError\)\)/);
  assert.match(app, /로컬 엔진 연결 후 대시보드를 불러오는 중/);
  assert.doesNotMatch(app, /ensureLocalEngineRecovered\(\)[\s\S]{0,240}dashboard/);
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
