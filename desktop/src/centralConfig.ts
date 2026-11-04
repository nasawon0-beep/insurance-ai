// 중앙(Supabase) 회원 설정. 값은 빌드 시 환경변수로 주입한다(하드코딩 없음).
// - VITE_MOADOOM_SUPABASE_URL: https://<project-ref>.supabase.co
// - VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY: sb_publishable_... (공개 클라이언트 키. secret/service_role 금지)
// - VITE_MOADOOM_LICENSE_PUBLIC_KEYS: {"<kid>":"<Ed25519 raw public key base64>"} (공개키만)
// - VITE_MOADOOM_SITE_URL: 가입/비밀번호 재설정/관리 화면이 있는 사이트 루트
// 세 값 중 하나라도 없으면 중앙 기능은 꺼지고 기존(이 PC 로컬 계정) 동작을 그대로 유지한다.
import { parsePublicKeys } from "./licenseToken";

const env = ((import.meta as any).env ?? {}) as Record<string, string | undefined>;

function clean(value: string | undefined): string {
  return (value ?? "").trim();
}

export const CENTRAL_URL = clean(env.VITE_MOADOOM_SUPABASE_URL).replace(/\/+$/, "");
export const CENTRAL_PUBLISHABLE_KEY = clean(env.VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY);
export const CENTRAL_LICENSE_PUBLIC_KEYS = parsePublicKeys(env.VITE_MOADOOM_LICENSE_PUBLIC_KEYS);
export const CENTRAL_SITE_URL = (clean(env.VITE_MOADOOM_SITE_URL) ||
  "https://nasawon0-beep.github.io/insurance-ai-updates/").replace(/\/?$/, "/");
// 모아둠 계정 신규 가입 열기("true"). 값이 없거나 "true"가 아니면 닫힘 — 기존 계정 로그인만(롤백용).
export const CENTRAL_SIGNUP_OPEN = clean(env.VITE_MOADOOM_SIGNUP_OPEN) === "true";

export function isPublishableKey(key: string): boolean {
  // secret/service_role 키를 실수로 넣으면 중앙 기능을 켜지 않는다.
  return key.startsWith("sb_publishable_") || (key.startsWith("eyJ") && !/service_role/.test(safeJwtRole(key)));
}

function safeJwtRole(jwt: string): string {
  try {
    const part = jwt.split(".")[1] ?? "";
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    return String(JSON.parse(json)?.role ?? "");
  } catch {
    return "service_role"; // 해석 불가한 JWT형 키는 거부
  }
}

export function centralEnabled(): boolean {
  // 로컬 QA 대역 서버(127.0.0.1)는 vite 개발 모드에서만 허용 — 배포 빌드에서는 항상 https supabase.co만.
  const devLocal = env.DEV === (true as any) && /^http:\/\/127\.0\.0\.1:\d+$/.test(CENTRAL_URL);
  return (
    (/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(CENTRAL_URL) || devLocal) &&
    isPublishableKey(CENTRAL_PUBLISHABLE_KEY) &&
    Object.keys(CENTRAL_LICENSE_PUBLIC_KEYS).length > 0
  );
}

export const CENTRAL_LICENSE_ENDPOINT = `${CENTRAL_URL}/functions/v1/moadoom-license`;
export const CENTRAL_ACCOUNT_URL = `${CENTRAL_SITE_URL}account/`;
export const CENTRAL_RESET_URL = `${CENTRAL_SITE_URL}account/reset.html`;
export const CENTRAL_ADMIN_URL = `${CENTRAL_SITE_URL}admin/`;
