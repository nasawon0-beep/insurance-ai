import { CONTROL_URL } from "./config.ts";

export function isFetchNetworkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Failed to fetch|NetworkError|Load failed/i.test(message);
}

export function formatLocalEngineNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `로컬 엔진 복구 중입니다. 잠시 후 자동으로 다시 시도됩니다. ` +
    `계속 실패하면 상단의 "로컬 엔진 재시작" 또는 "앱 재시작"을 눌러 주세요. (${message})`
  );
}

export function formatControlServerNetworkError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    `라이선스 서버(${CONTROL_URL})에 연결할 수 없습니다. ` +
    `유효한 오프라인 서명이 있으면 오프라인 모드로 계속 사용할 수 있습니다. (${message})`
  );
}
