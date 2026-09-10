#!/usr/bin/env node
// 버전 단일 정본 = desktop/package.json. 이 스크립트가 나머지 3파일에 그 값을 반영한다.
//   - desktop/src-tauri/tauri.conf.json  ("version")
//   - desktop/src-tauri/Cargo.toml       ([package] 섹션의 version)
//   - desktop/src-tauri/Cargo.lock       (name = "desktop-scaffold" 항목의 version)
// idempotent. 대상 위치를 정확히 1개 못 찾으면 exit 1 (조용히 통과하지 않는다).
// desktop/package.json 의 `version` npm 라이프사이클 훅에서 호출된다.
// 사용: node scripts/sync-version.mjs   (cwd 무관 — 경로는 이 파일 기준으로 해석)

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const P = {
  pkg: join(ROOT, "desktop/package.json"),
  tauri: join(ROOT, "desktop/src-tauri/tauri.conf.json"),
  cargoToml: join(ROOT, "desktop/src-tauri/Cargo.toml"),
  cargoLock: join(ROOT, "desktop/src-tauri/Cargo.lock"),
};

const die = (msg) => {
  console.error(`sync-version: ${msg}`);
  process.exit(1);
};

const version = JSON.parse(readFileSync(P.pkg, "utf8")).version;
if (typeof version !== "string" || !/^\d+\.\d+\.\d+$/.test(version)) {
  die(`desktop/package.json version 형식이 이상함: ${JSON.stringify(version)}`);
}

const changed = [];

// tauri.conf.json — JSON 파싱해 version 만 교체, 2-space + 개행 유지
{
  const obj = JSON.parse(readFileSync(P.tauri, "utf8"));
  if (typeof obj.version !== "string") die("tauri.conf.json 에 문자열 version 이 없음");
  if (obj.version !== version) {
    obj.version = version;
    writeFileSync(P.tauri, JSON.stringify(obj, null, 2) + "\n");
    changed.push("tauri.conf.json");
  }
}

// Cargo.toml — [package] 섹션만 잘라서 그 안의 단일 version 을 교체
{
  const raw = readFileSync(P.cargoToml, "utf8");
  const pkgSection = raw.match(/^\[package\]\n([\s\S]*?)(?=^\[|\Z)/m);
  if (!pkgSection) die("Cargo.toml 에서 [package] 섹션을 못 찾음");
  const verMatches = [...pkgSection[1].matchAll(/^version\s*=\s*"([^"]*)"/gm)];
  if (verMatches.length !== 1) {
    die(`Cargo.toml [package] 섹션의 version 줄이 ${verMatches.length}개 (1개여야 함)`);
  }
  if (verMatches[0][1] !== version) {
    const start = pkgSection.index + "[package]\n".length + verMatches[0].index;
    const next =
      raw.slice(0, start) +
      raw.slice(start).replace(/version\s*=\s*"[^"]*"/, `version = "${version}"`);
    writeFileSync(P.cargoToml, next);
    changed.push("Cargo.toml");
  }
}

// Cargo.lock — name = "desktop-scaffold" 바로 뒤의 version 줄만 (정확히 1개)
{
  const raw = readFileSync(P.cargoLock, "utf8");
  const re = /(name = "desktop-scaffold"\nversion = ")([^"]*)(")/g;
  const matches = [...raw.matchAll(re)];
  if (matches.length !== 1) {
    die(`Cargo.lock 의 desktop-scaffold version 항목이 ${matches.length}개 (1개여야 함)`);
  }
  if (matches[0][2] !== version) {
    writeFileSync(P.cargoLock, raw.replace(re, `$1${version}$3`));
    changed.push("Cargo.lock");
  }
}

console.log(
  changed.length
    ? `sync-version: ${version} → ${changed.join(", ")}`
    : `sync-version: 이미 모두 ${version}`,
);
