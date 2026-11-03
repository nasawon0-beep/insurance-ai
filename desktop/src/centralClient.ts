import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { CENTRAL_PUBLISHABLE_KEY, CENTRAL_URL, centralEnabled } from "./centralConfig";

// 세션은 기존 로컬 토큰과 같은 위치(앱 WebView 프로필 localStorage)에 둔다.
// 이유: 기존 5일 오프라인 사용 정책을 유지하려면 앱 재시작 후에도 회원 식별이 필요하다.
// (Windows 자격 증명 관리자 보관은 후속 과제로 결과 보고서에 기록)
let client: SupabaseClient | null = null;

export function centralClient(): SupabaseClient {
  if (!centralEnabled()) throw new Error("중앙 회원 설정이 없는 빌드입니다.");
  client ??= createClient(CENTRAL_URL, CENTRAL_PUBLISHABLE_KEY, {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: false,
      storageKey: "moadoom.central.session",
    },
  });
  return client;
}
