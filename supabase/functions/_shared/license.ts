// 모아둠 이용권 토큰 서명(서버 전용). Deno(Edge Function)와 Node 테스트에서 같은 코드가 돈다.
// 비대칭 Ed25519: 개인키는 Edge Function secret에만, 클라이언트에는 공개키만 둔다.
// 고정 개발 HMAC 키(dev-license-secret-change-me)를 대체한다.
// payload v2: 이용권 종류(trial/term/lifetime)와 정확한 시작·종료 시각(ISO). lifetime은 ends_at = null(명시적 무기한).

export type Entitlement = {
  user_id: string;
  role: "owner" | "admin" | null;
  effective_status: string;
  server_now: string;
  subscription: {
    status: string;
    kind: "trial" | "term" | "lifetime" | null;
    plan: string | null;
    starts_at: string | null;
    ends_at: string | null;
    suspended_at: string | null;
    trial_started_at: string | null;
  };
};

export type LicensePayload = {
  v: 2;
  kid: string;
  sub: string;
  email: string;
  role: "owner" | "admin" | null;
  kind: "trial" | "term" | "lifetime" | null;
  status: string; // none | scheduled | active | expired | suspended
  effective_status: string; // 서버 판정(소유자는 active)
  plan: string | null;
  starts_at: string | null;
  ends_at: string | null;
  suspended_at: string | null;
  trial_started_at: string | null;
  server_now: string;
  iat: number; // 서명 시각(초)
};

export type SignedLicense = { kid: string; payload: string; sig: string };

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const obj = value as Record<string, unknown>;
  return `{${Object.keys(obj).sort().map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
}

export function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64Decode(text: string): Uint8Array<ArrayBuffer> {
  const norm = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = norm + "=".repeat((4 - (norm.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function buildLicense(
  ent: Entitlement,
  user: { id: string; email: string },
  kid: string,
  nowSec: number,
): LicensePayload {
  if (ent.user_id !== user.id) throw new Error("entitlement user mismatch");
  const s = ent.subscription;
  if (s.kind === "lifetime" && s.ends_at !== null) throw new Error("lifetime must not have an end");
  return {
    v: 2,
    kid,
    sub: user.id,
    email: user.email,
    role: ent.role ?? null,
    kind: s.kind ?? null,
    status: s.status,
    effective_status: ent.effective_status,
    plan: s.plan ?? null,
    starts_at: s.starts_at ?? null,
    ends_at: s.ends_at ?? null,
    suspended_at: s.suspended_at ?? null,
    trial_started_at: s.trial_started_at ?? null,
    server_now: ent.server_now,
    iat: Math.floor(nowSec),
  };
}

export async function importSigningKey(pkcs8Base64: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("pkcs8", b64Decode(pkcs8Base64), { name: "Ed25519" }, false, ["sign"]);
}

export async function signLicense(payload: LicensePayload, key: CryptoKey): Promise<SignedLicense> {
  const bytes = new TextEncoder().encode(canonicalJson(payload));
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, key, bytes));
  return { kid: payload.kid, payload: b64urlEncode(bytes), sig: b64urlEncode(sig) };
}
