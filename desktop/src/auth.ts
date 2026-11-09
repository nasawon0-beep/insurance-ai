// control-server 연동: 로그인 / 라이선스 / 기기 등록 + 오프라인 유예.
// 아키텍처 3번: 시계 되돌리기 방지용 단조 타임스탬프를 로컬에 별도 저장.

import {
  CONTROL_URL,
  IS_NATIVE_RUNTIME,
  QA_INSTANCE_ID,
  QA_MODE,
  QA_PROFILE_FINGERPRINT,
} from "./config";
import { invoke } from "@tauri-apps/api/core";
import { responseFromNativeResult } from "./nativeResponse";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";
import { formatControlServerNetworkError, isFetchNetworkError } from "./localEngineErrors";
import { type SignedLicenseBlob, verifyLicenseForUser } from "./licenseSignature";
import {
  appAccessMode,
  CENTRAL_ACCOUNT_REQUIRED_MESSAGE,
  type LicenseResolution,
  type LicenseState,
  LOGIN_REQUIRED_MESSAGE,
  localSessionAllowed,
  SERVER_UNREACHABLE_MESSAGE,
} from "./licenseStatus";
import { CENTRAL_ACCOUNT_URL, CENTRAL_LICENSE_ENDPOINT, CENTRAL_LICENSE_PUBLIC_KEYS, CENTRAL_PUBLISHABLE_KEY, CENTRAL_RESET_URL, CENTRAL_SIGNUP_OPEN, centralEnabled } from "./centralConfig";
import { centralClient } from "./centralClient";
import { type LicenseFetchResult, resolveCentralLicenseWith } from "./centralLicenseFlow";
import type { SignedCentralLicense } from "./licenseToken";

export { CONTROL_URL };

const GRACE_DAYS = 5; // 오프라인 유예 (문서: 3~7일)

type NativeControlResponse = {
  status: number;
  body: number[];
  headers: [string, string][];
};

type User = { id: string; email: string };

/** control-server HTTP 오류 — 상태코드로 로그인 필요(401)와 서버 오류를 구분한다. */
export class ControlHttpError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ControlHttpError";
    this.status = status;
  }
}
const LS = {
  token: "iai.token",
  user: "iai.user",
  license: "iai.license.signed",
  deviceId: "iai.device_id",
  mono: "iai.mono_ts", // 단조 증가 타임스탬프 (초)
  provider: "iai.auth_provider", // "central" | "local"
  centralLicense: "moadoom.central.license",
};

export type AuthProvider = "central" | "local";

/** 현재 로그인 방식. 기존 설치본의 로컬 세션은 명시적으로 바꾸기 전까지 그대로 로컬로 본다. */
export function getAuthProvider(): AuthProvider | null {
  const v = localStorage.getItem(LS.provider);
  if (v === "central" && centralEnabled()) return "central";
  if (v === "local") return "local";
  return localStorage.getItem(LS.token) ? "local" : null;
}

/** 부팅 시 로그인 화면을 건너뛸 수 있는지(세션 또는 오프라인 판정 근거가 있는지). */
export function isSignedIn(): boolean {
  const provider = getAuthProvider();
  if (provider === "local") return !!localStorage.getItem(LS.token);
  if (provider === "central") return !!getUser();
  return false;
}

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

async function nativeControlFetch(path: string, init: RequestInit): Promise<Response> {
  if (init.body != null && typeof init.body !== "string") {
    throw new Error("control-server native request는 문자열 본문만 지원합니다");
  }
  const result = await invoke<NativeControlResponse>("control_server_request", {
    request: {
      method: init.method || "GET",
      path,
      headers: Array.from(new Headers(init.headers).entries()),
      body: typeof init.body === "string" ? { type: "text", text: init.body } : null,
    },
  });
  return responseFromNativeResult(result);
}

async function call(path: string, init?: RequestInit) {
  let res: Response;
  const requestInit: RequestInit = {
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: "Bearer " + getToken() } : {}),
    },
    ...init,
  };
  try {
    res = QA_MODE && IS_NATIVE_RUNTIME
      ? await nativeControlFetch(path, requestInit)
      : await fetch(`${CONTROL_URL}${path}`, requestInit);
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
    throw new ControlHttpError(formatErrorDetail(b?.detail) ?? `HTTP ${res.status}`, res.status);
  }
  return res.status === 204 ? null : res.json();
}

export async function changePassword(current_password: string, new_password: string) {
  if (getAuthProvider() === "central") {
    const sb = centralClient();
    const me = getUser();
    // 현재 비밀번호 재확인 후 변경
    const check = await sb.auth.signInWithPassword({ email: me?.email ?? "", password: current_password });
    if (check.error) throw new Error("현재 비밀번호가 올바르지 않습니다.");
    const { error } = await sb.auth.updateUser({ password: new_password });
    if (error) throw new Error(error.message);
    return { changed: true };
  }
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
  if (getAuthProvider() === "central") {
    const { data, error } = await centralClient().auth.getUser();
    if (error || !data.user?.email) throw new Error(error?.message ?? "회원 정보를 확인할 수 없습니다.");
    return { id: data.user.id, email: data.user.email, created_at: data.user.created_at };
  }
  const r = await call("/auth/me");
  return r.user;
}

export type AuthenticateResult = { user: User | null; requiresEmailConfirmation: boolean };

/** 중앙(모아둠 계정) 로그인/가입. 가입은 확인 메일을 거쳐야 로그인된다. */
async function authenticateCentral(mode: "login" | "register", email: string, password: string): Promise<AuthenticateResult> {
  const sb = centralClient();
  if (mode === "register") {
    if (!CENTRAL_SIGNUP_OPEN) throw new Error("모아둠 계정 신규 가입은 아직 열리지 않았습니다. 기존 계정으로 로그인하세요.");
    const { data, error } = await sb.auth.signUp({ email, password, options: { emailRedirectTo: CENTRAL_ACCOUNT_URL } });
    if (error) throw new Error(error.message);
    if (!data.session) return { user: null, requiresEmailConfirmation: true };
  } else {
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) {
      if (/email not confirmed/i.test(error.message)) {
        throw new Error("이메일 확인이 끝나지 않았습니다. 가입 확인 메일의 링크를 누른 뒤 다시 로그인하세요.");
      }
      throw new Error(/invalid login credentials/i.test(error.message) ? "이메일 또는 비밀번호가 올바르지 않습니다." : error.message);
    }
  }
  // 로컬 캐시가 아니라 Auth 서버가 확인한 사용자 정보를 쓴다.
  const { data, error } = await sb.auth.getUser();
  if (error || !data.user?.email) throw new Error("회원 정보를 확인할 수 없습니다.");
  const user = { id: data.user.id, email: data.user.email };
  const previous = getUser();
  if (previous?.id !== user.id) localStorage.removeItem(LS.centralLicense); // 다른 회원의 캐시 재사용 금지
  localStorage.setItem(LS.provider, "central");
  localStorage.setItem(LS.user, JSON.stringify(user));
  return { user, requiresEmailConfirmation: false };
}

export async function authenticate(
  mode: "login" | "register",
  email: string,
  password: string,
  provider: AuthProvider = centralEnabled() ? "central" : "local",
): Promise<AuthenticateResult> {
  if (provider === "central") return authenticateCentral(mode, email, password);
  // 중앙 회원 빌드: 이 PC 기존 계정은 이용권 근거가 아니다(로컬 이용권 우회 차단).
  // 로그인만 허용해 '자료 보존 모드'(기존 자료 조회·내보내기) 세션을 만든다. 가입·기기 등록·이용권 발급 없음.
  if (!localSessionAllowed(centralEnabled())) {
    if (mode !== "login") throw new Error(CENTRAL_ACCOUNT_REQUIRED_MESSAGE);
    const legacy = await call("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    localStorage.setItem(LS.provider, "local");
    localStorage.setItem(LS.token, legacy.token);
    localStorage.setItem(LS.user, JSON.stringify(legacy.user));
    return { user: legacy.user as User, requiresEmailConfirmation: false };
  }
  const r = await call(`/auth/${mode}`, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem(LS.provider, "local");
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
  return { user: r.user as User, requiresEmailConfirmation: false };
}

/** 로그아웃: 세션/캐시만 지운다. 고객 자료(local-engine DB/PDF)는 건드리지 않는다. */
export function logout() {
  // 로그아웃하면 모바일 연결 서버와 네트워크 변경 자동 재시작도 멈춘다('시작 시 복원' 설정은 다음 로그인용으로 유지).
  try {
    void invoke("mobile_sync_pause_for_logout").catch(() => undefined);
  } catch {
    /* Tauri 밖(테스트)에서는 무시 */
  }
  if (getAuthProvider() === "central") {
    void centralClient().auth.signOut({ scope: "local" }).catch(() => undefined);
  }
  [LS.token, LS.user, LS.license, LS.provider, LS.centralLicense, "iai.settings"].forEach((k) => localStorage.removeItem(k));
}

/** 비밀번호 재설정 메일 요청(중앙 계정). 메일의 링크는 사이트 재설정 화면으로 간다. */
export async function requestCentralPasswordReset(email: string): Promise<void> {
  const { error } = await centralClient().auth.resetPasswordForEmail(email, { redirectTo: CENTRAL_RESET_URL });
  if (error) throw new Error(error.message);
}

async function fetchCentralLicense(accessToken: string): Promise<LicenseFetchResult> {
  let res: Response;
  try {
    res = await fetch(CENTRAL_LICENSE_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        apikey: CENTRAL_PUBLISHABLE_KEY,
        "Content-Type": "application/json",
      },
      body: "{}",
      signal: AbortSignal.timeout(10000),
    });
  } catch (e) {
    return { kind: "network", message: e instanceof Error ? e.message : String(e) };
  }
  const body = await res.json().catch(() => null);
  if (!res.ok || !body?.license) return { kind: "http", status: res.status, error: body?.error };
  return { kind: "ok", license: body.license as SignedCentralLicense };
}

function resolveCentralLicense(): Promise<LicenseResolution> {
  return resolveCentralLicenseWith({
    async getSession() {
      const sb = centralClient();
      const { data } = await sb.auth.getSession();
      const s = data.session;
      if (!s?.access_token || !s.user?.email) return null;
      return { accessToken: s.access_token, userId: s.user.id, email: s.user.email };
    },
    fetchLicense: fetchCentralLicense,
    cachedUser: () => getUser(),
    readCache() {
      try {
        return JSON.parse(localStorage.getItem(LS.centralLicense) || "null");
      } catch {
        return null;
      }
    },
    writeCache: (license) => localStorage.setItem(LS.centralLicense, JSON.stringify(license)),
    publicKeys: CENTRAL_LICENSE_PUBLIC_KEYS,
    nowSec,
    clockRolledBack,
  });
}

/** 이 PC의 기존 로컬 계정을 중앙 계정에 연결(양쪽 비밀번호/세션을 각각 서버가 확인). 자료는 이동하지 않는다. */
export async function linkLocalAccount(localEmail: string, localPassword: string): Promise<{ linked: boolean; local_email: string }> {
  const { data } = await centralClient().auth.getSession();
  const centralToken = data.session?.access_token;
  if (!centralToken) throw new Error("모아둠 계정으로 먼저 로그인하세요.");
  const local = await call("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: localEmail.trim().toLowerCase(), password: localPassword }),
  });
  return call("/central-link", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${local.token}` },
    body: JSON.stringify({ central_access_token: centralToken }),
  });
}

/** control-server가 뜰 때까지 최대 maxMs 동안 대기 */
async function waitForControlServer(maxMs = 5000, intervalMs = 500): Promise<boolean> {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      const res = QA_MODE && IS_NATIVE_RUNTIME
        ? await nativeControlFetch("/health", { method: "GET" })
        : await fetch(`${CONTROL_URL}/health`, { method: "GET" });
      if (res.ok) {
        if (!QA_MODE) return true;
        const body = await res.json().catch(() => null);
        if (
          body?.control_server === "ok" &&
          body?.instance_id === QA_INSTANCE_ID &&
          body?.profile_fingerprint === QA_PROFILE_FINGERPRINT
        ) return true;
      }
    } catch {
      // 아직 안 뜸
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

/** 온라인이면 서버 라이선스, 오프라인이면 저장된 서명 블롭(유예 내). 실패는 null(구 호출부 호환). */
export async function resolveLicense(): Promise<{
  state: LicenseState;
  offline: boolean;
} | null> {
  const r = await resolveLicenseStatus();
  return r.kind === "ok" ? { state: r.state, offline: r.offline } : null;
}

/** 중앙 회원 빌드의 '자료 보존 모드' 세션 확인(이 PC 계정). 이용권을 만들지 않고, 로컬 자료는 건드리지 않는다. */
export async function resolveLegacyReadonlySession(): Promise<
  { kind: "readonly"; user: User } | { kind: "login_required"; message: string } | { kind: "server_unreachable"; message: string }
> {
  // 자료 보존 모드: control-server가 이 PC 계정 세션(로컬 비밀번호 로그인 결과)을 지금 다시 확인한 경우만.
  // 저장된 사용자 정보·로컬 이용권 캐시만으로는 열지 않는다.
  if (appAccessMode(centralEnabled(), getAuthProvider()) !== "readonly" || !getToken()) {
    return { kind: "login_required", message: CENTRAL_ACCOUNT_REQUIRED_MESSAGE };
  }
  try {
    await waitForControlServer(5000);
    const me = await call("/auth/me");
    if (!me?.user?.id) return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
    return { kind: "readonly", user: { id: me.user.id, email: me.user.email } as User };
  } catch (e) {
    if (e instanceof ControlHttpError && (e.status === 401 || e.status === 403)) {
      return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
    }
    return { kind: "server_unreachable", message: e instanceof Error ? e.message : String(e) };
  }
}

/** 만료/로그인 필요/서버 연결 실패를 구분해서 돌려준다. 어떤 경우에도 로컬 자료는 건드리지 않는다. */
export async function resolveLicenseStatus(): Promise<LicenseResolution> {
  if (getAuthProvider() === "central") return resolveCentralLicense();
  // 중앙 회원 빌드에 남아 있는 이전 로컬 세션/캐시(고정 개발 비밀로 서명된 로컬 이용권 포함)는 사용 근거로 쓰지 않는다.
  // 세션·고객 자료는 지우지 않고 로그인 화면으로 보낸다.
  if (!localSessionAllowed(centralEnabled())) return { kind: "login_required", message: CENTRAL_ACCOUNT_REQUIRED_MESSAGE };
  let onlineError: unknown = null;
  try {
    // control-server 준비 대기 (앱 시작 직후 타이밍 문제 방지)
    await waitForControlServer(5000);
    const lic = await call("/license");
    localStorage.setItem(LS.license, JSON.stringify(lic.signed));
    return { kind: "ok", state: lic, offline: false };
  } catch (e) {
    onlineError = e;
    if (e instanceof ControlHttpError && e.status === 401) {
      // 서버가 응답했고 세션이 무효 — 오프라인 캐시로 덮지 않는다.
      return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
    }
    console.warn("license refresh failed; trying cached offline license", {
      controlUrl: CONTROL_URL,
      errorName: e instanceof Error ? e.name : typeof e,
      message: e instanceof Error ? e.message : String(e),
    });
    const raw = localStorage.getItem(LS.license);
    if (!raw) {
      console.warn("offline license unavailable: no cached signed license", { controlUrl: CONTROL_URL });
      return { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE };
    }
    let blob: SignedLicenseBlob;
    try {
      blob = JSON.parse(raw) as SignedLicenseBlob;
    } catch {
      console.warn("offline license unavailable: cached signed license is not valid JSON", { controlUrl: CONTROL_URL });
      return { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE };
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
      return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
    }
    if (!(await verifyLicenseForUser(blob, licenseSecret, currentUserId))) {
      console.warn("offline license unavailable: signature or user check failed", { controlUrl: CONTROL_URL });
      return { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE };
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
      return { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE };
    }
    return { kind: "ok", state: blob.license as LicenseState, offline: true };
  }
}

export async function listDevices() {
  if (getAuthProvider() === "central") return [] as any[]; // 중앙 이용권은 기기 등록을 쓰지 않음(정책 미결)
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
