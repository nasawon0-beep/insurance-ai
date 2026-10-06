import { CONTROL_URL } from "./config.ts";

export function isFetchNetworkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Failed to fetch|NetworkError|Load failed|AbortError|abort|timed?\s*out/i.test(message);
}

export function formatLocalEngineNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `로컬 엔진 요청이 끊겼습니다. 진행 중인 분석이 있으면 잠시 기다린 뒤 다시 시도해 주세요. ` +
    `계속 실패할 때만 상단의 "로컬 엔진 재시작" 또는 "앱 재시작"을 눌러 주세요. (${message})`
  );
}

export function formatControlServerNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `라이선스 서버(${CONTROL_URL})에 연결할 수 없습니다. ` +
    `유효한 오프라인 서명이 있으면 오프라인 모드로 계속 사용할 수 있습니다. (${message})`
  );
}
