// 중앙 회원 빌드: '이 PC 기존 계정'(로컬 이용권, 고정 개발 비밀 서명)으로 중앙 이용권을 우회하지 못하고,
// 다계정 격리 정책: 로컬 로그인/자동 기존자료 연결 진입은 폐기한다. 남은 레거시 helper는 방어적 회귀로 유지.
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

test("account-isolated desktop rejects local provider before any login or license call", () => {
  const fn = body(auth, "export async function authenticate(");
  assert.match(fn, /if \(provider !== 'central' \|\| !centralEnabled\(\)\) throw new Error\(CENTRAL_ACCOUNT_REQUIRED_MESSAGE\)/);
  assert.match(fn, /return authenticateCentral\(mode, email, password\)/);
  assert.ok(!/call\(/.test(fn));
  const provider = body(auth, "export function getAuthProvider()");
  assert.ok(!/return "local"/.test(provider));
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

test("login screen has no PC-account/readonly entry or local password reset", () => {
  assert.doesNotMatch(login, /자료 보존 모드로 열기|setProvider\("local"\)|resetLocalPassword|listLocalAccounts/);
  assert.match(login, /useState<AuthProvider>\("central"\)/);
  assert.match(login, /requestCentralPasswordReset/);
});

test("linking still requires central session first", () => {
  const link = body(auth, "export async function linkLocalAccount(");
  assert.ok(link.indexOf("centralClient().auth.getSession()") < link.indexOf('call("/auth/login"'));
  assert.match(link, /if \(!centralToken\) throw/);
});
