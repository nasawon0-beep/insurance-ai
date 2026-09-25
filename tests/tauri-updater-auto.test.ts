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
    "https://nasangwon.github.io/insurance-ai-updates/api/windows.json",
  ]);
  assert.equal(config.plugins?.updater?.pubkey, "");
  assert.equal(config.bundle?.createUpdaterArtifacts, false);
});

test("desktop startup schedules one updater check after five seconds and asks before install", () => {
  const app = read("desktop/src/App.tsx");
  const rust = read("desktop/src-tauri/src/lib.rs");

  assert.match(app, /setTimeout\([\s\S]*5000\)/);
  assert.match(app, /window\.confirm\([\s\S]*업데이트[\s\S]*설치/);
  assert.match(app, /installUpdate\(result\.update\)/);
  assert.match(app, /clearTimeout\(timer\)/);

  assert.match(rust, /use tauri_plugin_updater::UpdaterExt;/);
  assert.match(rust, /tauri_plugin_updater::Builder::new\(\)\.build\(\)/);
});
