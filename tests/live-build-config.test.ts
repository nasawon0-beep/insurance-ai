// live 빌드 설정(공개값) 검증 + 앱 중앙 설정 판정이 켜지는지 확인
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { validateLiveConfig } from "../scripts/build-moadoom-live.mjs";

const cfg = JSON.parse(readFileSync(new URL("../desktop/moadoom-live.public.json", import.meta.url), "utf8"));

test("live public config is valid and contains only public values", () => {
  assert.deepEqual(validateLiveConfig(cfg), []);
  assert.equal(cfg.VITE_MOADOOM_SUPABASE_URL, "https://fhdushntttxbfwkxylhz.supabase.co");
  assert.deepEqual(JSON.parse(cfg.VITE_MOADOOM_LICENSE_PUBLIC_KEYS), { "moadoom-20261103": "A9f5O+g778LZrvn73WCntWNGIPCzeUpP4fDDjdfKENc=" });
  assert.equal(cfg.VITE_MOADOOM_SITE_URL, "https://nasawon0-beep.github.io/insurance-ai-updates/");
  assert.equal(cfg.VITE_MOADOOM_SIGNUP_OPEN, "false");
});

test("validator rejects secret keys, extra keys and malformed public keys", () => {
  assert.ok(validateLiveConfig({ ...cfg, VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY: "sb_" + "secret_" + "abcdefghijklmnopqrstuvwxyz" }).length > 0);
  assert.ok(validateLiveConfig({ ...cfg, MOADOOM_LICENSE_SIGNING_KEY: "x" }).length > 0);
  assert.ok(validateLiveConfig({ ...cfg, VITE_MOADOOM_LICENSE_PUBLIC_KEYS: '{"k":"AAAA"}' }).length > 0);
  assert.ok(validateLiveConfig({ ...cfg, VITE_MOADOOM_SUPABASE_URL: "http://127.0.0.1:54399" }).length > 0);
});

test("release workflow injects central public config before frontend/tauri build", () => {
  const wf = readFileSync(new URL("../.github/workflows/release.yml", import.meta.url), "utf8");
  const load = wf.indexOf("node scripts/build-moadoom-live.mjs github-env");
  assert.ok(load > 0, "load step missing");
  assert.ok(load < wf.indexOf("npm run build"), "must run before npm run build");
  assert.ok(load < wf.indexOf("npm run tauri -- build"), "must run before tauri build");
});
