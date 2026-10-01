// control-server 연동: 로그인 / 라이선스 / 기기 등록 + 오프라인 유예.
// 아키텍처 3번: 시계 되돌리기 방지용 단조 타임스탬프를 로컬에 별도 저장.

import { CONTROL_URL } from "./config";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";
import { formatControlServerNetworkError, isFetchNetworkError } from "./localEngineErrors";
import { type SignedLicenseBlob, verifyLicenseForUser } from "./licenseSignature";

export { CONTROL_URL };

const GRACE_DAYS = 5; // 오프라인 유예 (문서: 3~7일)

type User = { id: string; email: string };
type LicenseState = {
  plan: string;
  device_limit: number;
  expiry: string | null;
  status: string;
  days_left: number | null;
};
const LS = {
  token: "iai.token",
  user: "iai.user",
  license: "iai.license.signed",
  deviceId: "iai.device_id",
  mono: "iai.mono_ts", // 단조 증가 타임스탬프 (초)
};

function nowSec() {
  return Math.floor(Date.now() / 1000);
}

/** 매 호출마다 단조 시계를 갱신하고, 되돌아갔으면 true 반환 */
export function clockRolledBack(): boolean {
  const prev = Number(localStorage.getItem(LS.mono) || 0);
  const now = nowSec();
  if (now + 120 < prev) return true; // 2분 이상 과거로 점프 = 되돌리기
  localStorage.setItem(LS.mono, String(Math.max(prev, now)));
  return false;
}

export function getToken() {
  return localStorage.getItem(LS.token);
}
export function getUser(): User | null {
  const s = localStorage.getItem(LS.user);
  return s ? JSON.parse(s) : null;
}
export function deviceId(): string {
  let d = localStorage.getItem(LS.deviceId);
  if (!d) {
    d = (crypto as any).randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2);
    localStorage.setItem(LS.deviceId, d);
  }
  return d;
}

async function call(path: string, init?: RequestInit) {
  let res: Response;
  try {
    res = await fetch(`${CONTROL_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      ...init,
    });
  } catch (e) {
    if (isFetchNetworkError(e)) {
      console.warn("control-server network request failed", {
        url: `${CONTROL_URL}${path}`,
        errorName: e instanceof Error ? e.name : typeof e,
        message: e instanceof Error ? e.message : String(e),
      });
      throw new Error(formatControlServerNetworkError(e));
    }
    throw e;
  }
  if (!res.ok) {
    const b = await res.json().catch(() => null);
    console.warn("control-server HTTP request failed", {
      url: `${CONTROL_URL}${path}`,
      status: res.status,
      detail: b?.detail ?? null,
    });
    throw new Error(formatErrorDetail(b?.detail) ?? `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

export async function changePassword(current_password: string, new_password: string) {
  return call("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

export async function localRecoveryStatus(): Promise<boolean> {
  try {
    const r = await call("/auth/local-recovery/status");
    return !!r?.available;
  } catch {
    return false;
  }
}

export async function listLocalAccounts(): Promise<{ email: string; created_at?: string }[]> {
  const r = await call("/auth/local-recovery/accounts");
  return r?.accounts ?? [];
}

export async function resetLocalPassword(email: string, new_password: string) {
  return call("/auth/local-recovery/reset", {
    method: "POST",
    body: JSON.stringify({ email, new_password }),
  });
}

export async function fetchMe(): Promise<{ id: string; email: string; created_at?: string }> {
  const r = await call("/auth/me");
  return r.user;
}

export async function authenticate(mode: "login" | "register", email: string, password: string) {
  const r = await call(`/auth/${mode}`, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem(LS.token, r.token);
  localStorage.setItem(LS.user, JSON.stringify(r.user));
  // 기기 등록 (실패해도 로그인은 유지 — 초과 시 메시지만)
  try {
    await call("/devices", {
      method: "POST",
      body: JSON.stringify({
        device_id: deviceId(),
        name: navigator.platform || "desktop",
        app_version: "0.1.0",
      }),
    });
  } catch (e) {
    console.warn("device register:", e);
  }
  return r.user as User;
}

export function logout() {
  [LS.token, LS.user, LS.license, "iai.settings"].forEach((k) => localStorage.removeItem(k));
}

/** control-server가 뜰 때까지 최대 maxMs 동안 대기 */
async function waitForControlServer(maxMs = 5000, intervalMs = 500): Promise<boolean> {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${CONTROL_URL}/health`, { method: "GET" });
      if (res.ok) return true;
    } catch {
      // 아직 안 뜸
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

/** 온라인이면 서버 라이선스, 오프라인이면 저장된 서명 블롭(유예 내) */
export async function resolveLicense(): Promise<{
  state: LicenseState;
  offline: boolean;
} | null> {
  let onlineError: unknown = null;
  try {
    // control-server 준비 대기 (앱 시작 직후 타이밍 문제 방지)
    await waitForControlServer(5000);
    const lic = await call("/license");
    localStorage.setItem(LS.license, JSON.stringify(lic.signed));
    return { state: lic, offline: false };
  } catch (e) {
    onlineError = e;
    console.warn("license refresh failed; trying cached offline license", {
      controlUrl: CONTROL_URL,
      errorName: e instanceof Error ? e.name : typeof e,
      message: e instanceof Error ? e.message : String(e),
    });
    const raw = localStorage.getItem(LS.license);
    if (!raw) {
      console.warn("offline license unavailable: no cached signed license", { controlUrl: CONTROL_URL });
      return null;
    }
    let blob: SignedLicenseBlob;
    try {
      blob = JSON.parse(raw) as SignedLicenseBlob;
    } catch {
      console.warn("offline license unavailable: cached signed license is not valid JSON", { controlUrl: CONTROL_URL });
      return null;
    }
    const licenseSecret =
      (import.meta as any).env?.VITE_LICENSE_SECRET || "dev-license-secret-change-me";
    if (licenseSecret === "dev-license-secret-change-me") {
      console.warn("VITE_LICENSE_SECRET is unset; using insecure development secret");
    }
    let currentUserId: unknown;
    try {
      currentUserId = getUser()?.id;
    } catch {
      console.warn("offline license unavailable: cached user is not readable", { controlUrl: CONTROL_URL });
      return null;
    }
    if (!(await verifyLicenseForUser(blob, licenseSecret, currentUserId))) {
      console.warn("offline license unavailable: signature or user check failed", { controlUrl: CONTROL_URL });
      return null;
    }
    const ageDays = (nowSec() - blob.signed_at) / 86400;
    const rolledBack = clockRolledBack();
    if (rolledBack || ageDays > GRACE_DAYS || ageDays < 0) {
      console.warn("offline license unavailable: grace check failed", {
        controlUrl: CONTROL_URL,
        ageDays: Math.round(ageDays * 100) / 100,
        graceDays: GRACE_DAYS,
        clockRolledBack: rolledBack,
        onlineError: onlineError instanceof Error ? onlineError.message : String(onlineError),
      });
      return null;
    }
    return { state: blob.license as LicenseState, offline: true };
  }
}

export async function listDevices() {
  return (await call("/devices")).devices as any[];
}
export async function removeDevice(id: string) {
  await call(`/devices/${id}`, { method: "DELETE" });
}

// ---- 로컬 엔진 설정 (RRN 파일럿 토글 등) ----
const LS_SETTINGS = "iai.settings";

const SETTINGS_DEFAULT = { rrn_input_enabled: false, source: "default" };

/** 로그인/부팅 시 1회 호출 → localStorage["iai.settings"] 갱신.
 * 실패 시엔 fail-safe 로 OFF 기본값을 강제 기록한다 (이전 세션의 stale ON 방지). */
export async function loadEngineSettings(): Promise<void> {
  try {
    const r = await engineFetch("/settings");
    if (r.ok) {
      localStorage.setItem(LS_SETTINGS, JSON.stringify(await r.json()));
      return;
    }
  } catch {
    /* 엔진 미기동 */
  }
  localStorage.setItem(LS_SETTINGS, JSON.stringify(SETTINGS_DEFAULT));
}

export function engineSettings(): { rrn_input_enabled?: boolean; source?: string } {
  try {
    return JSON.parse(localStorage.getItem(LS_SETTINGS) || "{}");
  } catch {
    return {};
  }
}

/** 주민등록번호 입력 UI 를 그릴지 여부. 항상 true (토글 제거됨). */
export function rrnEnabled(): boolean {
  return true;
}
