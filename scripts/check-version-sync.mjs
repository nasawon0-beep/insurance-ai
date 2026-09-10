#!/usr/bin/env node
// 4개 파일의 앱 버전이 일치하는지 검사. 불일치·파싱실패·형식이상이면 exit 1. CI(ci.yml)에서 실행.
// 정본: desktop/package.json. 나머지는 scripts/sync-version.mjs 로 맞춘다.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(ROOT, p), "utf8");

function cargoTomlVersion(text) {
  const pkg = text.match(/^\[package\]\n([\s\S]*?)(?=^\[|\Z)/m);
  if (!pkg) return undefined;
  const m = [...pkg[1].matchAll(/^version\s*=\s*"([^"]*)"/gm)];
  return m.length === 1 ? m[0][1] : undefined;
}

function cargoLockVersion(text) {
  const m = [...text.matchAll(/name = "desktop-scaffold"\nversion = "([^"]*)"/g)];
  return m.length === 1 ? m[0][1] : undefined;
}

const found = {
  "desktop/package.json": JSON.parse(read("desktop/package.json")).version,
  "desktop/src-tauri/tauri.conf.json": JSON.parse(
    read("desktop/src-tauri/tauri.conf.json"),
  ).version,
  "desktop/src-tauri/Cargo.toml": cargoTomlVersion(read("desktop/src-tauri/Cargo.toml")),
  "desktop/src-tauri/Cargo.lock": cargoLockVersion(read("desktop/src-tauri/Cargo.lock")),
};

for (const [f, v] of Object.entries(found)) console.log(`  ${v ?? "(못 찾음)"}\t${f}`);

const canonical = found["desktop/package.json"];
let fail = false;

if (typeof canonical !== "string" || !/^\d+\.\d+\.\d+$/.test(canonical)) {
  console.error(`\n✗ 정본 버전(desktop/package.json) 형식 이상: ${JSON.stringify(canonical)}`);
  fail = true;
}
const mismatched = Object.entries(found).filter(([, v]) => v !== canonical);
if (mismatched.length) {
  console.error(
    `\n✗ 버전 불일치 / 미검출. 정본=${canonical}. ` +
      `\`node scripts/sync-version.mjs\` 실행 후 커밋하세요.`,
  );
  fail = true;
}

if (fail) process.exit(1);
console.log(`\n✓ 4파일 모두 ${canonical}`);
