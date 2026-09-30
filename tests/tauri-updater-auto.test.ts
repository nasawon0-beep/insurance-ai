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
