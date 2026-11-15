// 모아둠 live(실제 Supabase) 연결 빌드 실행기 — 공개값만 주입한다.
// 설정: desktop/moadoom-live.public.json (공개값만, workspace 검증 공개키 필수)
// 유지보수 공개키는 별도 선택 설정이며 없으면 유지보수 기능은 계속 차단된다.
// 사용(저장소 루트에서):
//   node scripts/build-moadoom-live.mjs check      # 설정 검증만
//   node scripts/build-moadoom-live.mjs frontend   # desktop: npm run build (tsc + vite) → desktop/dist
//   node scripts/build-moadoom-live.mjs tauri      # desktop: npx tauri build (설치본 생성 — 팀장 담당)
// 개인키/서비스키는 이 설정에 들어갈 수 없다(검증 실패 시 빌드 중단).
import { appendFileSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const desktop = path.join(root, "desktop");
const cfgPath = path.join(desktop, "moadoom-live.public.json");

export function validateLiveConfig(cfg) {
  const errors = [];
  const allowed = ["VITE_MOADOOM_SUPABASE_URL", "VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY", "VITE_MOADOOM_LICENSE_PUBLIC_KEYS", "VITE_MOADOOM_SITE_URL", "VITE_MOADOOM_SIGNUP_OPEN"];
  for (const k of Object.keys(cfg)) if (!allowed.includes(k)) errors.push(`unexpected key ${k}`);
  for (const k of allowed) if (typeof cfg[k] !== "string" || !cfg[k]) errors.push(`missing ${k}`);
  const text = JSON.stringify(cfg);
  if (/sb_secret_|service_role|PRIVATE KEY|SIGNING_KEY|eyJhbGciOi/.test(text)) errors.push("secret-like value present");
  if (!/^https:\/\/[a-z0-9]{20}\.supabase\.co$/.test(cfg.VITE_MOADOOM_SUPABASE_URL ?? "")) errors.push("bad supabase url");
  if (!/^sb_publishable_[A-Za-z0-9_-]{20,}$/.test(cfg.VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY ?? "")) errors.push("not a publishable key");
  try {
    const keys = JSON.parse(cfg.VITE_MOADOOM_LICENSE_PUBLIC_KEYS ?? "");
    const entries = Object.entries(keys);
    if (!entries.length) errors.push("no license public keys");
    for (const [kid, pub] of entries) {
      const raw = Buffer.from(String(pub), "base64");
      if (!/^[A-Za-z0-9._-]{3,64}$/.test(kid) || raw.length !== 32) errors.push(`bad public key ${kid} (Ed25519 raw 32 bytes expected)`);
    }
  } catch {
    errors.push("license public keys not JSON");
  }
  if (!/^https:\/\/[^/]+\/.*\/$/.test(cfg.VITE_MOADOOM_SITE_URL ?? "")) errors.push("site url must be https and end with /");
  if (!["true", "false"].includes(cfg.VITE_MOADOOM_SIGNUP_OPEN)) errors.push("VITE_MOADOOM_SIGNUP_OPEN must be \"true\" or \"false\"");
  return errors;
}

// Integrated account-isolation builds need a separately provisioned workspace verifier.
// Keeping legacy validation separate permits auditing previously approved public config
// without pretending it is sufficient for the new native workspace gate.
export function validateIntegratedConfig(cfg) {
  const { VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS: workspace, VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS: maintenance, ...legacy } = cfg;
  const errors = validateLiveConfig(legacy);
  for (const [name, value, required] of [
    ["VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS", workspace, true],
    ["VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS", maintenance, false],
  ]) {
    if (value === undefined && !required) continue;
    try {
      if (typeof value !== "string") throw new Error();
      const keys = JSON.parse(value);
      if (!keys || typeof keys !== "object" || Array.isArray(keys) || !Object.keys(keys).length) throw new Error();
      for (const [kid, pub] of Object.entries(keys)) {
        if (!/^[A-Za-z0-9._-]{3,64}$/.test(kid) || typeof pub !== "string") throw new Error();
        const raw = Buffer.from(pub, "base64");
        if (raw.length !== 32 || raw.toString("base64") !== pub) throw new Error();
      }
    } catch { errors.push(`missing or invalid ${name} (separate Ed25519 public keys required)`); }
  }
  return errors;
}

export function verifyRepositoryMaps(cfg, env) {
  for (const name of ["VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS", "VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS"]) {
    try {
      const supplied = JSON.parse(env[name] || "");
      const expected = JSON.parse(cfg[name]);
      if (!supplied || Array.isArray(supplied) || typeof supplied !== "object" || !Object.keys(supplied).length ||
          Object.keys(supplied).sort().join("\n") !== Object.keys(expected).sort().join("\n") ||
          Object.keys(expected).some(key => supplied[key] !== expected[key])) throw new Error();
    } catch { throw new Error(`${name}: required repository map missing, invalid or different from committed configuration`); }
  }
}

function main() {
  const mode = process.argv[2] ?? "check";
  const names = ["VITE_MOADOOM_SUPABASE_URL", "VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY", "VITE_MOADOOM_LICENSE_PUBLIC_KEYS", "VITE_MOADOOM_SITE_URL", "VITE_MOADOOM_SIGNUP_OPEN", "VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS", "VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS"];
  // Check what Vite AND Cargo will inherit, not a JSON file that wasn't injected.
  const cfg = mode === "check-env"
    ? Object.fromEntries(names.filter(name => process.env[name] !== undefined).map(name => [name, process.env[name]]))
    : JSON.parse(readFileSync(cfgPath, "utf8"));
  const errors = validateIntegratedConfig(cfg);
  if (errors.length) {
    console.error("live config invalid:", errors.join("; "));
    process.exit(2);
  }
  if (mode === "github-env") {
    try { verifyRepositoryMaps(cfg, process.env); }
    catch (error) { console.error(error.message); process.exit(2); }
  }
  console.log("live config OK (values suppressed)");
  if (mode === "check" || mode === "check-env") return;
  if (mode === "github-env") {
    // Export canonical committed values only after required vars match; never log values.
    const out = process.env.GITHUB_ENV;
    if (!out) {
      console.error("GITHUB_ENV not set");
      process.exit(2);
    }
    appendFileSync(out, Object.entries(cfg).map(([k, v]) => `${k}=${v}\n`).join(""));
    console.log(`exported ${Object.keys(cfg).length} public build values to GITHUB_ENV`);
    return;
  }
  const cmd = mode === "frontend" ? "npm run build" : mode === "tauri" ? "npx tauri build" : null;
  if (!cmd) {
    console.error("usage: check | frontend | tauri");
    process.exit(2);
  }
  const r = spawnSync(cmd, { cwd: desktop, stdio: "inherit", shell: true, env: { ...process.env, ...cfg } });
  process.exit(r.status ?? 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
