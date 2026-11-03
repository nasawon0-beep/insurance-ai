// 로컬 HTTP E2E: 같은 회원으로 분리된 클라이언트 2개(PC-A, PC-B)가 실제 데스크톱 판정 코드로 이용권을 받는다.
// 대상: supabase/tests/qa_emulator.py (로컬 대역 — 실제 Supabase 아님). 실행:
//   node --experimental-strip-types supabase/tests/two_clients_http.ts http://127.0.0.1:54399 <public.json> <member-email> <member-pw> <owner-email> <owner-pw>
// 전제: 회원은 사이트에서 가입·이메일 확인(자동 무료체험) 완료, 소유자는 seed_owner.sql로 지정됨.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { resolveCentralLicenseWith } from "../../desktop/src/centralLicenseFlow.ts";
import { licenseBanner, licenseNotice, subscriptionLabel } from "../../desktop/src/licenseStatus.ts";

const [BASE, PUBFILE, MEMBER, MEMBER_PW, OWNER, OWNER_PW] = process.argv.slice(2);
const pub = JSON.parse(readFileSync(PUBFILE, "utf8"));
const KEYS = { [pub.kid]: pub.public_key };
const H = { apikey: "sb_publishable_qa_local_only", "Content-Type": "application/json" };
const DAY = 86400;
const log: string[] = [];
const step = (s: string) => { log.push(s); console.log("✔", s); };

async function login(email: string, password: string) {
  const r = await fetch(`${BASE}/auth/v1/token?grant_type=password`, { method: "POST", headers: H, body: JSON.stringify({ email, password }) });
  const j: any = await r.json();
  assert.equal(r.status, 200, JSON.stringify(j));
  return { accessToken: j.access_token as string, userId: j.user.id as string, email: j.user.email as string };
}
async function rpc(token: string | null, fn: string, args: object = {}) {
  const r = await fetch(`${BASE}/rest/v1/rpc/${fn}`, {
    method: "POST", headers: token ? { ...H, Authorization: `Bearer ${token}` } : H, body: JSON.stringify(args),
  });
  return { status: r.status, body: await r.json().catch(() => null) as any };
}
async function setDown(down: boolean) {
  await fetch(`${BASE}/__qa/license-down`, { method: "POST", headers: H, body: JSON.stringify({ down }) });
}

class Pc {
  cache: unknown = null;
  offline = false;
  clock = 0;
  name: string;
  session: { accessToken: string; userId: string; email: string };
  constructor(name: string, session: { accessToken: string; userId: string; email: string }) {
    this.name = name;
    this.session = session;
  }
  resolve() {
    return resolveCentralLicenseWith({
      getSession: async () => this.session,
      fetchLicense: async (token) => {
        if (this.offline) return { kind: "network", message: "offline" };
        try {
          const r = await fetch(`${BASE}/functions/v1/moadoom-license`, { method: "POST", headers: { ...H, Authorization: `Bearer ${token}` }, body: "{}" });
          const b: any = await r.json().catch(() => null);
          if (!r.ok || !b?.license) return { kind: "http", status: r.status, error: b?.error };
          return { kind: "ok", license: b.license };
        } catch (e) {
          return { kind: "network", message: String(e) };
        }
      },
      cachedUser: () => ({ id: this.session.userId, email: this.session.email }),
      readCache: () => this.cache as any,
      writeCache: (l) => { this.cache = l; },
      publicKeys: KEYS,
      nowSec: () => Date.now() / 1000 + this.clock,
      clockRolledBack: () => false,
    });
  }
}
async function state(pc: Pc) {
  const r = await pc.resolve();
  return r.kind === "ok"
    ? { kind: "ok", eff: r.state.effective_status, type: r.state.kind, end: r.state.ends_at, offline: r.offline,
        label: subscriptionLabel(r.state), banner: licenseBanner(r.state), notice: licenseNotice(r.state, r.offline) }
    : { kind: r.kind, message: (r as any).message };
}
const change = (tok: string, target: string, action: string, extra: object = {}) =>
  rpc(tok, "moadoom_admin_change_subscription", { target, action, ends_on: null, days: null, reason: `QA ${action}`, ...extra });

const owner = await login(OWNER, OWNER_PW);
const memberA = await login(MEMBER, MEMBER_PW);
const memberB = await login(MEMBER, MEMBER_PW); // 별도 로그인 세션(다른 PC)
assert.notEqual(memberA.accessToken, memberB.accessToken);
const pcA = new Pc("PC-A", memberA);
const pcB = new Pc("PC-B", memberB);

// 0) 거부: 익명, 일반 회원 관리자 RPC/타인 조회(IDOR), 자기 무기한/기간 발급, 익명 이용권
assert.ok((await rpc(null, "moadoom_my_entitlement")).status >= 400);
assert.ok((await rpc(null, "moadoom_admin_search_members", { q: "" })).status >= 400);
assert.equal((await rpc(memberA.accessToken, "moadoom_admin_member_detail", { target: owner.userId })).status, 403);
assert.equal((await rpc(memberA.accessToken, "moadoom_admin_search_members", { q: "" })).status, 403);
assert.equal((await change(memberA.accessToken, memberA.userId, "lifetime")).status, 403);
assert.equal((await change(memberA.accessToken, memberA.userId, "adjust", { days: 365 })).status, 403);
assert.equal((await change(owner.accessToken, owner.userId, "lifetime")).status, 403); // 소유자도 자기 자신은 불가
assert.equal((await fetch(`${BASE}/functions/v1/moadoom-license`, { method: "POST", headers: H, body: "{}" })).status, 401);
step("거부: 익명 RPC, 회원→관리자 검색/상세(IDOR) 403, 회원 자기 무기한/연장 403, 소유자 자기 변경 403, 익명 이용권 401");

// 1) 현재 상태(사이트 관리자 화면에서 지정한 상태)를 두 PC가 같은 값으로 본다
let a: any = await state(pcA), b: any = await state(pcB);
assert.equal(a.kind, "ok"); assert.equal(a.eff, b.eff); assert.equal(a.type, b.type); assert.equal(a.end, b.end);
step(`초기 동일 반영: PC-A ${a.label} / PC-B ${b.label} (종료 ${a.end ?? "없음"})`);

// 2) 무기한 → 두 PC 무기한(종료 없음), 역할 없음
assert.equal((await change(owner.accessToken, memberA.userId, "lifetime")).status, 200);
a = await state(pcA); b = await state(pcB);
assert.deepEqual([a.type, a.end, a.eff, b.type, b.end, b.eff], ["lifetime", null, "active", "lifetime", null, "active"]);
const me = await rpc(memberA.accessToken, "moadoom_my_entitlement");
assert.equal(me.body.role, null);
assert.equal((await rpc(memberA.accessToken, "moadoom_admin_search_members", { q: "" })).status, 403);
step(`무기한 지정 반영: PC-A "${a.label}" / PC-B "${b.label}", role=null, 관리 RPC 여전히 403`);

// 3) 무기한도 서명·오프라인 5일 규칙: 서버 장애 시 5일 내 사용, 6일째 server_unreachable(만료 아님)
await setDown(true);
a = await state(pcA);
assert.deepEqual([a.kind, a.eff, a.offline], ["ok", "active", true]);
pcA.clock = 6 * DAY;
a = await state(pcA);
assert.equal(a.kind, "server_unreachable"); assert.match(a.message, /이용 기간 만료가 아니며/);
pcA.clock = 0;
assert.equal((await state(new Pc("PC-C(캐시 없음)", memberA))).kind, "server_unreachable");
await setDown(false);
step("서버 장애 구분: 무기한도 캐시 5일 내 오프라인, 6일째 server_unreachable(만료 문구 아님), 캐시 없는 PC도 server_unreachable");

// 4) 무기한 해제 → 기간지정(오늘+20일 KST 끝) → 두 PC 같은 종료 시각
const endsOn = new Date(Date.now() + 9 * 3600e3 + 20 * DAY * 1000).toISOString().slice(0, 10);
assert.equal((await change(owner.accessToken, memberA.userId, "term", { ends_on: endsOn })).status, 200);
a = await state(pcA); b = await state(pcB);
assert.equal(a.type, "term"); assert.equal(a.end, b.end);
assert.equal(new Date(Date.parse(a.end) + 9 * 3600e3).toISOString().slice(0, 16), `${new Date(Date.parse(endsOn) + DAY * 1000).toISOString().slice(0, 10)}T00:00`);
step(`무기한 해제→기간지정 ${endsOn}: 두 PC 종료 ${a.end} (= ${endsOn} 다음날 00:00 KST)`);

// 5) 연장 10일 / 단축 3일
assert.equal((await change(owner.accessToken, memberA.userId, "adjust", { days: 10 })).status, 200);
const afterExt: any = await state(pcB);
assert.equal(Date.parse(afterExt.end) - Date.parse(a.end), 10 * DAY * 1000);
assert.equal((await change(owner.accessToken, memberA.userId, "adjust", { days: -3 })).status, 200);
a = await state(pcA); b = await state(pcB);
assert.equal(a.end, b.end); assert.equal(Date.parse(a.end) - Date.parse(afterExt.end), -3 * DAY * 1000);
step(`연장 +10일 / 단축 -3일 반영: 두 PC 종료 ${a.end}`);

// 6) 이용정지 → 두 PC suspended, 자료 보존 안내 / 해제 → 기간 유지
assert.equal((await change(owner.accessToken, memberA.userId, "suspend")).status, 200);
a = await state(pcA); b = await state(pcB);
assert.deepEqual([a.eff, b.eff], ["suspended", "suspended"]); assert.match(String(a.banner), /보존/);
assert.equal((await change(owner.accessToken, memberA.userId, "resume")).status, 200);
b = await state(pcB);
assert.equal(b.eff, "active"); assert.equal(b.end, a.end);
step(`이용정지 반영: "${a.banner}" / 해제 후 PC-B active, 종료 유지 ${b.end}`);

// 7) 재로그인(새 세션)으로 체험/기간 초기화 불가 — 같은 종료 시각
const memberC = await login(MEMBER, MEMBER_PW);
const c: any = await state(new Pc("PC-A 재설치", memberC));
assert.equal(c.end, b.end); assert.equal(c.type, "term");
step("재로그인/재설치(새 세션, 캐시 없음) 후에도 같은 이용권 — 초기화 없음");

// 8) 감사 이력: 처리자/사유/전후 상태
const detail = await rpc(owner.accessToken, "moadoom_admin_member_detail", { target: memberA.userId });
const actions = detail.body.audit.map((x: any) => x.action);
assert.deepEqual(actions.slice(0, 6), ["resume", "suspend", "adjust", "adjust", "term", "lifetime"]);
assert.ok(actions.includes("trial_auto"));
assert.ok(detail.body.audit.filter((x: any) => x.action !== "trial_auto").every((x: any) => x.actor_email === OWNER && x.reason));
step(`감사 이력: ${actions.join(",")} (처리자 ${OWNER}, 자동체험=시스템)`);

// 9) 다시 무기한으로 두고 끝(사이트 화면 확인용)
assert.equal((await change(owner.accessToken, memberA.userId, "lifetime")).status, 200);
a = await state(pcA); b = await state(pcB);
assert.deepEqual([a.type, b.type], ["lifetime", "lifetime"]);
step("최종: 무기한 무료 재지정, 두 PC lifetime");

// 10) 소유자 계정은 이용권 없이 active(운영 권한과 이용권 분리)
assert.equal(((await state(new Pc("owner-PC", owner))) as any).eff, "active");
step("소유자: 이용권과 무관하게 active");

console.log(`RESULT PASS ${log.length} steps`);
