// 중앙 이용권 토큰 검증(클라이언트). 공개키만 사용한다 — 서명용 개인키는 서버(Edge Function secret)에만 있다.
// 오프라인 허용 기간은 기존 정책(GRACE_DAYS=5)을 그대로 쓴다. 단, 유예는 "서버 확인 없이 쓸 수 있는 기간"일 뿐이고
// 체험/기간 이용의 종료는 서명된 종료 시각(ends_at)으로 지금 시각에 다시 판정한다(유예로 종료가 늘어나지 않음).

export type CentralLicensePayload = {
  v: 2;
  kid: string;
  sub: string;
  email: string;
  role: "owner" | "admin" | null;
  kind: "trial" | "term" | "lifetime" | null;
  status: string; // 서명 시점 서버 판정: none | scheduled | active | expired | suspended
  effective_status: string;
  plan: string | null;
  starts_at: string | null; // ISO 시각
  ends_at: string | null; // ISO 시각, lifetime은 null
  suspended_at: string | null;
  trial_started_at: string | null;
  server_now: string;
  iat: number;
};

export type SignedCentralLicense = { kid: string; payload: string; sig: string };

export type LicenseCheck =
  | { ok: true; payload: CentralLicensePayload; effective: string; ageSec: number }
  | { ok: false; reason: "unknown_key" | "bad_signature" | "malformed" | "wrong_user" | "stale" | "future" | "crypto_unavailable" };

export const GRACE_DAYS = 5;

function b64Decode(text: string): Uint8Array<ArrayBuffer> {
  const norm = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = norm + "=".repeat((4 - (norm.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function sec(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t / 1000;
}

/** 지금 시각 기준 남은 초(lifetime/없음은 null). */
export function remainingSeconds(p: CentralLicensePayload, nowSec: number): number | null {
  const end = sec(p.ends_at);
  return end == null ? null : Math.max(0, Math.floor(end - nowSec));
}

/** 클라이언트 시각으로 이용권 상태를 다시 계산. 서버가 이미 끝났다고 서명한 상태는 되돌리지 않는다. */
export function effectiveAt(p: CentralLicensePayload, nowSec: number): string {
  if (p.role === "owner") return "active";
  if (p.status === "suspended" || p.status === "revoked" || p.status === "none" || p.status === "expired") return p.status;
  const start = sec(p.starts_at);
  const end = sec(p.ends_at);
  if (end != null && nowSec >= end) return "expired";
  if (start != null && nowSec < start) return "scheduled";
  return "active";
}

export async function verifyCentralLicense(
  signed: SignedCentralLicense,
  publicKeys: Record<string, string>,
  expectedSub: string,
  nowSec: number,
  options: { offline: boolean; clockRolledBack?: boolean; graceDays?: number } = { offline: false },
): Promise<LicenseCheck> {
  if (!signed || typeof signed.payload !== "string" || typeof signed.sig !== "string") {
    return { ok: false, reason: "malformed" };
  }
  const raw = publicKeys[signed.kid];
  if (!raw) return { ok: false, reason: "unknown_key" };
  let key: CryptoKey;
  try {
    key = await crypto.subtle.importKey("raw", b64Decode(raw), { name: "Ed25519" }, false, ["verify"]);
  } catch {
    return { ok: false, reason: "crypto_unavailable" };
  }
  let payloadBytes: Uint8Array<ArrayBuffer>;
  let sig: Uint8Array<ArrayBuffer>;
  try {
    payloadBytes = b64Decode(signed.payload);
    sig = b64Decode(signed.sig);
  } catch {
    return { ok: false, reason: "malformed" };
  }
  const valid = await crypto.subtle.verify({ name: "Ed25519" }, key, sig, payloadBytes);
  if (!valid) return { ok: false, reason: "bad_signature" };
  let payload: CentralLicensePayload;
  try {
    payload = JSON.parse(new TextDecoder().decode(payloadBytes));
  } catch {
    return { ok: false, reason: "malformed" };
  }
  if (payload.v !== 2 || payload.kid !== signed.kid) return { ok: false, reason: "malformed" };
  if (payload.kind === "lifetime" && payload.ends_at !== null) return { ok: false, reason: "malformed" };
  if (payload.sub !== expectedSub) return { ok: false, reason: "wrong_user" };
  const ageSec = nowSec - payload.iat;
  if (ageSec < -300) return { ok: false, reason: "future" };
  if (options.offline) {
    const grace = (options.graceDays ?? GRACE_DAYS) * 86400;
    if (options.clockRolledBack || ageSec > grace) return { ok: false, reason: "stale" };
  }
  return { ok: true, payload, effective: effectiveAt(payload, nowSec), ageSec };
}

export function parsePublicKeys(raw: string | undefined | null): Record<string, string> {
  if (!raw) return {};
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      return Object.fromEntries(
        Object.entries(obj).filter(([k, v]) => typeof k === "string" && typeof v === "string" && v.length > 0),
      ) as Record<string, string>;
    }
  } catch {
    /* 잘못된 설정은 비활성으로 취급 */
  }
  return {};
}
