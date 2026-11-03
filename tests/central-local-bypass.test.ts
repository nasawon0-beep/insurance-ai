// 중앙 회원 빌드: '이 PC 기존 계정'(로컬 이용권, 고정 개발 비밀 서명)으로 중앙 이용권을 우회하지 못하고,
// 대신 로컬 비밀번호 인증 후 기존 자료 조회·내보내기만 하는 '자료 보존 모드'로 연다. 로컬 전용 빌드는 기존 동작.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { appAccessMode, CENTRAL_ACCOUNT_REQUIRED_MESSAGE, LEGACY_READONLY_NOTICE, localSessionAllowed } from "../desktop/src/licenseStatus.ts";

const src = (p: string) => readFileSync(new URL(`../desktop/src/${p}`, import.meta.url), "utf8");
const auth = src("auth.ts");
const login = src("LoginScreen.tsx");
const app = src("App.tsx");
const view = src("LegacyReadonlyView.tsx");

function body(text: string, signature: string): string {
  const start = text.indexOf(signature);
  assert.ok(start >= 0, signature);
  const next = text.indexOf("\nexport ", start + signature.length);
  return text.slice(start, next < 0 ? undefined : next);
}

test("policy: local license only in local-only builds; central build + PC account => readonly", () => {
  assert.equal(localSessionAllowed(true), false);
  assert.equal(localSessionAllowed(false), true);
  assert.equal(appAccessMode(true, "local"), "readonly");
  assert.equal(appAccessMode(true, "central"), "license");
  assert.equal(appAccessMode(true, null), "license");
  assert.equal(appAccessMode(false, "local"), "license");
  assert.match(CENTRAL_ACCOUNT_REQUIRED_MESSAGE, /고객 자료는 이 PC에 그대로/);
  assert.match(LEGACY_READONLY_NOTICE, /조회와 내보내기만/);
});

test("resolveLicenseStatus still refuses local/cached dev-signed license in central build", () => {
  const fn = body(auth, "export async function resolveLicenseStatus()");
  const guard = fn.indexOf('if (!localSessionAllowed(centralEnabled())) return { kind: "login_required", message: CENTRAL_ACCOUNT_REQUIRED_MESSAGE };');
  assert.ok(guard > 0, "guard missing");
  assert.ok(guard > fn.indexOf('getAuthProvider() === "central"'), "central path must stay first");
  assert.ok(guard < fn.indexOf('call("/license")'));
  assert.ok(guard < fn.indexOf("localStorage.getItem(LS.license)"));
});

test("central build PC-account login: password checked by control-server, login only, no device/license", () => {
  const fn = body(auth, "export async function authenticate(");
  const branch = fn.slice(fn.indexOf("if (!localSessionAllowed(centralEnabled())) {"), fn.indexOf("const r = await call(`/auth/${mode}`"));
  assert.ok(branch.length > 0);
  assert.match(branch, /if \(mode !== "login"\) throw new Error\(CENTRAL_ACCOUNT_REQUIRED_MESSAGE\);/);
  assert.match(branch, /call\("\/auth\/login"/);
  assert.ok(!/\/devices|\/license|\/auth\/register/.test(branch));
});

test("readonly session is re-verified by control-server /auth/me, never from cached user/license alone", () => {
  const fn = body(auth, "export async function resolveLegacyReadonlySession()");
  assert.match(fn, /appAccessMode\(centralEnabled\(\), getAuthProvider\(\)\) !== "readonly" \|\| !getToken\(\)/);
  assert.match(fn, /call\("\/auth\/me"\)/);
  assert.ok(!/LS\.license|verifyLicenseForUser|removeItem/.test(fn));
  assert.match(fn, /e\.status === 401 \|\| e\.status === 403/);
});

test("App routes readonly sessions to LegacyReadonlyView before license resolution; full app only for 'in'", () => {
  const ro = app.indexOf('if (appAccessMode(centralEnabled(), getAuthProvider()) === "readonly") {');
  assert.ok(ro > 0 && ro < app.indexOf("const lic = await resolveLicenseStatus();", ro));
  assert.match(app, /if \(auth === "readonly"\) \{\s*return <LegacyReadonlyView /);
  assert.ok(app.indexOf('if (auth === "readonly")') < app.indexOf("if (needsOnboarding)"));
});

test("LegacyReadonlyView only issues GET engine requests (no create/update/delete/analysis/AI)", () => {
  const calls = [...view.matchAll(/getJson\(([^)]*)\)/g)].map((m) => m[1]).filter((c) => c !== "path: string");
  assert.ok(calls.length >= 4, calls.join("|"));
  for (const c of calls) assert.match(c, /^[`"]\/(customers|export|exports)/, c);
  assert.match(view, /engineFetch\(path, \{ method: "GET" \}\)/);
  assert.equal((view.match(/engineFetch\(/g) || []).length, 1, "engineFetch only via getJson");
  assert.ok(!/method:\s*"(POST|PUT|PATCH|DELETE)"/.test(view));
  assert.ok(!/\/(analy|assistant|rag|parse|stt|coverage|intake|rrn)/.test(view));
});

test("login screen: central build PC tab opens readonly mode, no unauthenticated local reset", () => {
  assert.match(login, /"자료 보존 모드로 열기"/);
  assert.match(login, /\{central && provider === "local" \? null : !showRecovery \?/);
  assert.match(login, /!\(central && provider === "local"\) && !\(central && provider === "central" && !CENTRAL_SIGNUP_OPEN\)/);
});

test("linking still requires central session first", () => {
  const link = body(auth, "export async function linkLocalAccount(");
  assert.ok(link.indexOf("centralClient().auth.getSession()") < link.indexOf('call("/auth/login"'));
  assert.match(link, /if \(!centralToken\) throw/);
});
