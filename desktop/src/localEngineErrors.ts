import { CONTROL_URL, LOCAL_ENGINE_URL } from "./config.ts";

export function isFetchNetworkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Failed to fetch|NetworkError|Load failed/i.test(message);
}

export function formatLocalEngineNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `local-engine API(${LOCAL_ENGINE_URL})에 연결할 수 없습니다. ` +
    `/health가 정상이어도 인증(/api-secret) 또는 분석 API 호출이 실패했을 수 있습니다. ` +
    `잠시 후 다시 분석하거나 앱 재시작을 시도해 주세요. (${message})`
  );
}

export function formatControlServerNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `라이선스 서버(${CONTROL_URL})에 연결할 수 없습니다. ` +
    `유효한 오프라인 서명이 있으면 오프라인 모드로 계속 사용할 수 있습니다. (${message})`
  );
}
