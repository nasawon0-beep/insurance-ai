// 중앙 회원 빌드에서 '이 PC 기존 계정'(로컬 이용권, 고정 개발 비밀 서명) 경로로 중앙 이용권을 우회하지 못해야 한다.
// 로컬 전용 빌드는 기존 동작 유지, 자료/세션 삭제 없음, 연결은 중앙 로그인 후에만.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CENTRAL_ACCOUNT_REQUIRED_MESSAGE, localSessionAllowed } from "../desktop/src/licenseStatus.ts";

const auth = readFileSync(new URL("../desktop/src/auth.ts", import.meta.url), "utf8");
const login = readFileSync(new URL("../desktop/src/LoginScreen.tsx", import.meta.url), "utf8");

function body(src: string, signature: string): string {
  const start = src.indexOf(signature);
  assert.ok(start >= 0, signature);
  const next = src.indexOf("\nexport ", start + signature.length);
  return src.slice(start, next < 0 ? undefined : next);
}

test("policy: local session only allowed in local-only builds", () => {
  assert.equal(localSessionAllowed(true), false);
  assert.equal(localSessionAllowed(false), true);
  assert.match(CENTRAL_ACCOUNT_REQUIRED_MESSAGE, /고객 자료는 이 PC에 그대로/);
});

test("resolveLicenseStatus refuses local/cached dev-signed license before any local check in central build", () => {
  const fn = body(auth, "export async function resolveLicenseStatus()");
  const guard = fn.indexOf('if (!localSessionAllowed(centralEnabled())) return { kind: "login_required", message: CENTRAL_ACCOUNT_REQUIRED_MESSAGE };');
  assert.ok(guard > 0, "guard missing");
  assert.ok(guard > fn.indexOf('getAuthProvider() === "central"'), "central path must stay first");
  assert.ok(guard < fn.indexOf('call("/license")'), "guard must run before local /license");
  assert.ok(guard < fn.indexOf("localStorage.getItem(LS.license)"), "guard must run before cached offline license");
  assert.ok(!/removeItem/.test(fn.slice(0, guard + 200)), "no session/data deletion in guard");
});

test("authenticate never creates a local session in central build; linking still verifies local password after central login", () => {
  const fn = body(auth, "export async function authenticate(");
  const guard = fn.indexOf("if (!localSessionAllowed(centralEnabled())) throw new Error(CENTRAL_ACCOUNT_REQUIRED_MESSAGE);");
  assert.ok(guard > 0 && guard < fn.indexOf("call(`/auth/${mode}`"));
  const link = body(auth, "export async function linkLocalAccount(");
  assert.ok(link.indexOf("centralClient().auth.getSession()") < link.indexOf('call("/auth/login"'), "central session checked first");
  assert.match(link, /if \(!centralToken\) throw/);
});

test("login screen disables local-only login in central build and explains linking", () => {
  assert.match(login, /disabled=\{busy \|\| \(central && provider === "local"\)\}/);
  assert.match(login, /이 PC 기존 계정만으로는 로그인할 수 없습니다/);
});
