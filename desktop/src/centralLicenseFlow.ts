// 중앙 이용권 판정 흐름(순수 로직 — 네트워크/저장소는 주입). Node 테스트와 앱이 같은 코드를 쓴다.
// 결과는 만료·회수·미발급(=이용권 상태)과 로그인 필요·서버 연결 실패(=접속 상태)를 구분한다.
// 어떤 경우에도 고객 자료를 지우지 않는다 — 판정만 돌려준다.
import { type LicenseResolution, type LicenseState, LOGIN_REQUIRED_MESSAGE, SERVER_UNREACHABLE_MESSAGE } from "./licenseStatus.ts";
import { type CentralLicensePayload, remainingSeconds, type SignedCentralLicense, verifyCentralLicense } from "./licenseToken.ts";

export const EMAIL_NOT_CONFIRMED_MESSAGE = "이메일 확인이 끝나지 않았습니다. 가입 확인 메일의 링크를 누른 뒤 다시 로그인하세요.";
export const CACHE_INVALID_MESSAGE =
  "인증 서버에 연결할 수 없고, 이 PC에 저장된 이용권 확인 기록도 검증되지 않았습니다(변조 또는 다른 계정). 연결된 상태에서 다시 로그인하세요. 구독 만료가 아니며 고객 자료는 그대로 있습니다.";
export const LICENSE_SIGNATURE_MESSAGE =
  "이용권 확인 응답의 서명이 올바르지 않습니다. 앱을 최신 버전으로 업데이트하거나 관리자에게 문의하세요. 고객 자료는 그대로 있습니다.";

export type CentralSession = { accessToken: string; userId: string; email: string } | null;

export type LicenseFetchResult =
  | { kind: "ok"; license: SignedCentralLicense }
  | { kind: "http"; status: number; error?: string }
  | { kind: "network"; message: string };

export type CentralDeps = {
  getSession(): Promise<CentralSession>;
  fetchLicense(accessToken: string): Promise<LicenseFetchResult>;
  cachedUser(): { id: string; email: string } | null;
  readCache(): SignedCentralLicense | null;
  writeCache(license: SignedCentralLicense): void;
  publicKeys: Record<string, string>;
  nowSec(): number;
  clockRolledBack(): boolean;
};

export function toLicenseState(p: CentralLicensePayload, effective: string, nowSec: number): LicenseState {
  const remaining = remainingSeconds(p, nowSec);
  return {
    plan: p.plan ?? "모아둠",
    device_limit: 0,
    expiry: p.ends_at,
    status: p.status,
    days_left: remaining == null ? null : Math.ceil(remaining / 86400),
    remaining_seconds: remaining,
    starts_at: p.starts_at,
    ends_at: p.ends_at,
    suspended_at: p.suspended_at,
    trial_started_at: p.trial_started_at,
    kind: p.kind,
    role: p.role,
    effective_status: effective,
  };
}

async function fromCache(deps: CentralDeps, sub: string | null): Promise<LicenseResolution> {
  const cached = deps.readCache();
  if (!cached || !sub) return { kind: "server_unreachable", message: SERVER_UNREACHABLE_MESSAGE };
  const now = deps.nowSec();
  const check = await verifyCentralLicense(cached, deps.publicKeys, sub, now, {
    offline: true,
    clockRolledBack: deps.clockRolledBack(),
  });
  if (!check.ok) {
    const message = check.reason === "stale" ? SERVER_UNREACHABLE_MESSAGE : CACHE_INVALID_MESSAGE;
    return { kind: "server_unreachable", message };
  }
  return { kind: "ok", state: toLicenseState(check.payload, check.effective, now), offline: true };
}

export async function resolveCentralLicenseWith(deps: CentralDeps): Promise<LicenseResolution> {
  let session: CentralSession = null;
  try {
    session = await deps.getSession();
  } catch {
    session = null;
  }
  const cachedUser = deps.cachedUser();
  if (!session) {
    // 세션 갱신이 네트워크 때문에 실패했을 수 있다 → 이전에 검증된 사용자 기준으로 오프라인 판정
    if (cachedUser) return fromCache(deps, cachedUser.id);
    return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
  }
  const result = await deps.fetchLicense(session.accessToken);
  if (result.kind === "network") return fromCache(deps, session.userId);
  if (result.kind === "http") {
    if (result.status === 401) return { kind: "login_required", message: LOGIN_REQUIRED_MESSAGE };
    if (result.status === 403 && result.error === "email_not_confirmed") {
      return { kind: "login_required", message: EMAIL_NOT_CONFIRMED_MESSAGE };
    }
    return fromCache(deps, session.userId); // 5xx 등 서버 장애 → 만료로 표시하지 않음
  }
  const now = deps.nowSec();
  const check = await verifyCentralLicense(result.license, deps.publicKeys, session.userId, now, { offline: false });
  if (!check.ok) return { kind: "server_unreachable", message: LICENSE_SIGNATURE_MESSAGE };
  deps.writeCache(result.license);
  return { kind: "ok", state: toLicenseState(check.payload, check.effective, now), offline: false };
}
