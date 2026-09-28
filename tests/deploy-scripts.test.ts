import assert from "node:assert/strict";
import { statSync, readFileSync } from "node:fs";
import { test } from "node:test";

const root = new URL("..", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");
const mode = (path: string) => statSync(new URL(path, root)).mode & 0o777;

test("Windows deployment script builds, releases, and updates GitHub Pages metadata", () => {
  const script = read("scripts/deploy-windows.sh");

  assert.equal(script.startsWith("#!/bin/bash\nset -e"), true);
  assert.match(script, /Usage: \.\/deploy-windows\.sh 2\.3\.0/);
  assert.match(script, /cd desktop[\s\S]*CI=true npm run tauri build/);
  assert.match(script, /PLATFORM="windows"/);
  assert.match(script, /gh release create v\$\{VERSION\}-\$\{PLATFORM\}[\s\S]*--title "\$\{PLATFORM\^\} v\$\{VERSION\}"/);
  assert.match(script, /cd ~\/insurance-ai-updates/);
  assert.match(script, /api\/windows\.json <<EOF[\s\S]*"version": "\$\{VERSION\}"/);
  assert.match(script, /"url": "\$\{DOWNLOAD_URL\}"/);
  assert.equal((mode("scripts/deploy-windows.sh") & 0o111) !== 0, true);
});

test("Android deployment script builds, releases, and updates GitHub Pages metadata", () => {
  const script = read("scripts/deploy-android.sh");

  assert.equal(script.startsWith("#!/bin/bash\nset -e"), true);
  assert.match(script, /Usage: \.\/deploy-android\.sh 1\.1\.0/);
  assert.match(script, /cd android[\s\S]*\.\/gradlew assembleRelease/);
  assert.match(script, /gh release create v\$\{VERSION\}-android[\s\S]*--title "Android v\$\{VERSION\}"/);
  assert.match(script, /cd ~\/insurance-ai-updates/);
  assert.match(script, /api\/android\.json <<EOF[\s\S]*"latest_version": "\$\{VERSION\}"/);
  assert.match(script, /"download_url": "\$\{DOWNLOAD_URL\}"/);
  assert.equal((mode("scripts/deploy-android.sh") & 0o111) !== 0, true);
});

test("deployment README documents usage for both release scripts", () => {
  const readme = read("scripts/README.md");

  assert.match(readme, /\.\/scripts\/deploy-windows\.sh 2\.3\.0/);
  assert.match(readme, /\.\/scripts\/deploy-android\.sh 1\.1\.0/);
  assert.match(readme, /GitHub Pages/);
});
