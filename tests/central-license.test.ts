// 중앙 이용권 토큰: 서버 서명(supabase/functions/_shared/license.ts) ↔ 클라이언트 검증(desktop/src/licenseToken.ts)
// 실제 Ed25519 WebCrypto로 서명/검증한다. 서버(Supabase)는 대역(mock)이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildLicense, type Entitlement, signLicense } from "../supabase/functions/_shared/license.ts";
import { effectiveAt, verifyCentralLicense } from "../desktop/src/licenseToken.ts";
import { CACHE_INVALID_MESSAGE, type CentralDeps, resolveCentralLicenseWith } from "../desktop/src/centralLicenseFlow.ts";
import { licenseBanner, licenseNotice, LOGIN_REQUIRED_MESSAGE, SERVER_UNREACHABLE_MESSAGE } from "../desktop/src/licenseStatus.ts";

const USER = { id: "11111111-1111-4111-8111-111111111111", email: "member@example.test" };
const OTHER = "22222222-2222-4222-8222-222222222222";
const DAY = 86400;
const NOW = Date.UTC(2026, 10, 3, 3, 0, 0) / 1000; // 2026-11-03 12:00 KST
const iso = (s: number) => new Date(s * 1000).toISOString();

async function keypair() {
  const kp = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
  return { privateKey: kp.privateKey, publicB64: Buffer.from(raw).toString("base64") };
}

function ent(over: Partial<Entitlement["subscription"]> = {}, role: Entitlement["role"] = null): Entitlement {
  const sub = { status: "active", kind: "trial" as const, plan: "TRIAL", starts_at: iso(NOW - DAY), ends_at: iso(NOW + 6 * DAY),
    suspended_at: null, trial_started_at: iso(NOW - DAY), ...over };
  return { user_id: USER.id, role, effective_status: role === "owner" ? "active" : sub.status, server_now: iso(NOW), subscription: sub };
}

test("signed v2 license verifies with the public key only", async () => {
  const { privateKey, publicB64 } = await keypair();
  const signed = await signLicense(buildLicense(ent(), USER, "k1", NOW), privateKey);
  const r = await verifyCentralLicense(signed, { k1: publicB64 }, USER.id, NOW);
  assert.ok(r.ok);
  if (r.ok) assert.deepEqual([r.payload.v, r.payload.kind, r.effective], [2, "trial", "active"]);
});

test("tampered payload (e.g. trial->lifetime), bad signature, unknown key and other user are rejected", async () => {
  const { privateKey, publicB64 } = await keypair();
  const other = await keypair();
  const signed = await signLicense(buildLicense(ent({ status: "expired", ends_at: iso(NOW - 60) }), USER, "k1", NOW), privateKey);
  const forged = JSON.parse(Buffer.from(signed.payload, "base64url").toString());
  Object.assign(forged, { kind: "lifetime", ends_at: null, status: "active", effective_status: "active" });
  const tampered = { ...signed, payload: Buffer.from(JSON.stringify(forged)).toString("base64url") };
  assert.deepEqual(await verifyCentralLicense(tampered, { k1: publicB64 }, USER.id, NOW), { ok: false, reason: "bad_signature" });
  const badSig = { ...signed, sig: signed.sig.slice(0, -2) + (signed.sig.endsWith("AA") ? "BB" : "AA") };
  assert.equal((await verifyCentralLicense(badSig, { k1: publicB64 }, USER.id, NOW)).ok, false);
  assert.deepEqual(await verifyCentralLicense(signed, { k2: publicB64 }, USER.id, NOW), { ok: false, reason: "unknown_key" });
  assert.deepEqual(await verifyCentralLicense(signed, { k1: other.publicB64 }, USER.id, NOW), { ok: false, reason: "bad_signature" });
  assert.deepEqual(await verifyCentralLicense(signed, { k1: publicB64 }, OTHER, NOW), { ok: false, reason: "wrong_user" });
  assert.throws(() => buildLicense(ent({ kind: "lifetime", ends_at: iso(NOW + DAY) }), USER, "k1", NOW), /lifetime/);
});

test("trial end is evaluated at the exact second, and offline grace never extends it", async () => {
  const { privateKey, publicB64 } = await keypair();
  const end = NOW + 2 * DAY;
  const p = buildLicense(ent({ ends_at: iso(end) }), USER, "k1", NOW);
  assert.equal(effectiveAt(p, end - 1), "active");
  assert.equal(effectiveAt(p, end), "expired");
  const signed = await signLicense(p, privateKey);
  // 서명 3일 뒤(유예 5일 이내) 오프라인: 체험은 2일째에 이미 끝남 → 만료로 판정
  const r = await verifyCentralLicense(signed, { k1: publicB64 }, USER.id, NOW + 3 * DAY, { offline: true });
  assert.ok(r.ok && r.effective === "expired");
  // 서버가 이미 만료라고 서명한 상태는 시계를 되돌려도 활성이 되지 않는다
  const exp = buildLicense(ent({ status: "expired", ends_at: iso(NOW - 10) }), USER, "k1", NOW);
  assert.equal(effectiveAt(exp, NOW - 100), "expired");
});

test("lifetime still needs signature + online refresh within 5 days; suspension wins", async () => {
  const { privateKey, publicB64 } = await keypair();
  const keys = { k1: publicB64 };
  const life = await signLicense(buildLicense(ent({ kind: "lifetime", plan: "FREE", ends_at: null }), USER, "k1", NOW), privateKey);
  assert.ok((await verifyCentralLicense(life, keys, USER.id, NOW + 4.9 * DAY, { offline: true })).ok);
  assert.deepEqual(await verifyCentralLicense(life, keys, USER.id, NOW + 5.1 * DAY, { offline: true }), { ok: false, reason: "stale" });
  assert.deepEqual(await verifyCentralLicense(life, keys, USER.id, NOW + DAY, { offline: true, clockRolledBack: true }), { ok: false, reason: "stale" });
  assert.deepEqual(await verifyCentralLicense(life, keys, USER.id, NOW - DAY, { offline: true }), { ok: false, reason: "future" });
  const susp = buildLicense(ent({ kind: "lifetime", ends_at: null, status: "suspended", suspended_at: iso(NOW) }), USER, "k1", NOW);
  assert.equal(effectiveAt(susp, NOW), "suspended");
  const owner = buildLicense(ent({ status: "expired", ends_at: iso(NOW - 10) }, "owner"), USER, "k1", NOW);
  assert.equal(effectiveAt(owner, NOW + 30 * DAY), "active");
});

function deps(over: Partial<CentralDeps> & { keys: Record<string, string> }): CentralDeps & { cache: unknown[] } {
  const cache: unknown[] = [];
  return {
    cache,
    getSession: async () => ({ accessToken: "at", userId: USER.id, email: USER.email }),
    fetchLicense: async () => ({ kind: "network", message: "offline" }),
    cachedUser: () => USER,
    readCache: () => (cache.at(-1) as any) ?? null,
    writeCache: (l) => cache.push(l),
    publicKeys: over.keys,
    nowSec: () => NOW,
    clockRolledBack: () => false,
    ...over,
  };
}

test("flow distinguishes expiry, login required, email unconfirmed and server failure", async () => {
  const { privateKey, publicB64 } = await keypair();
  const keys = { k1: publicB64 };
  const expired = await signLicense(buildLicense(ent({ status: "expired", ends_at: iso(NOW - 60) }), USER, "k1", NOW), privateKey);
  const ok = await resolveCentralLicenseWith(deps({ keys, fetchLicense: async () => ({ kind: "ok", license: expired }) }));
  assert.ok(ok.kind === "ok");
  if (ok.kind === "ok") {
    assert.equal(ok.state.effective_status, "expired");
    assert.match(licenseBanner(ok.state) ?? "", /무료체험 기간이 끝났습니다.*고객 자료는 그대로 보존/);
  }
  assert.deepEqual(await resolveCentralLicenseWith(deps({ keys, fetchLicense: async () => ({ kind: "http", status: 401 }) })),
    { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE });
  assert.equal((await resolveCentralLicenseWith(deps({ keys, fetchLicense: async () => ({ kind: "http", status: 403, error: "email_not_confirmed" }) }))).kind, "login_required");
  assert.deepEqual(await resolveCentralLicenseWith(deps({ keys, fetchLicense: async () => ({ kind: "http", status: 503 }) })),
    { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE });
  assert.equal((await resolveCentralLicenseWith(deps({ keys, getSession: async () => null, cachedUser: () => null }))).kind, "login_required");
});

test("network failure: verified cache within 5 days, trial end still enforced, never another user's cache", async () => {
  const { privateKey, publicB64 } = await keypair();
  const keys = { k1: publicB64 };
  const active = await signLicense(buildLicense(ent({ ends_at: iso(NOW + 2 * DAY) }), USER, "k1", NOW), privateKey);
  const d = deps({ keys, fetchLicense: async () => ({ kind: "ok", license: active }) });
  const first = await resolveCentralLicenseWith(d);
  assert.ok(first.kind === "ok" && first.state.kind === "trial" && first.state.remaining_seconds === 2 * DAY);
  assert.match(licenseNotice(first.state, false) ?? "", /무료체험 중 · 남은 기간 2일/);
  const off1 = await resolveCentralLicenseWith({ ...d, fetchLicense: async () => ({ kind: "network", message: "x" }), nowSec: () => NOW + DAY });
  assert.ok(off1.kind === "ok" && off1.offline && off1.state.effective_status === "active");
  const off3 = await resolveCentralLicenseWith({ ...d, fetchLicense: async () => ({ kind: "network", message: "x" }), nowSec: () => NOW + 3 * DAY });
  assert.ok(off3.kind === "ok" && off3.offline && off3.state.effective_status === "expired"); // 유예가 체험을 늘리지 않음
  const tooOld = await resolveCentralLicenseWith({ ...d, fetchLicense: async () => ({ kind: "network", message: "x" }), nowSec: () => NOW + 6 * DAY });
  assert.deepEqual(tooOld, { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE });
  const otherUser = await resolveCentralLicenseWith({ ...d, getSession: async () => ({ accessToken: "at", userId: OTHER, email: "x@example.test" }),
    fetchLicense: async () => ({ kind: "network", message: "x" }) });
  assert.equal(otherUser.kind, "server_unreachable");
  const forged = { ...active, sig: active.sig.replace(/^./, active.sig[0] === "A" ? "B" : "A") };
  assert.equal((await resolveCentralLicenseWith({ ...d, fetchLicense: async () => ({ kind: "ok", license: forged }) })).kind, "server_unreachable");
  assert.deepEqual(await resolveCentralLicenseWith(deps({ keys, readCache: () => forged })), { kind: "server_unreachable", message: CACHE_INVALID_MESSAGE });
});
