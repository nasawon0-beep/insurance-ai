import assert from "node:assert/strict";
import test from "node:test";

import {
  formatControlServerNetworkError,
  formatLocalEngineNetworkError,
  isFetchNetworkError,
} from "../desktop/src/localEngineErrors.ts";

test("local-engine network error gives actionable local recovery without naming the license server", () => {
  const msg = formatLocalEngineNetworkError(new TypeError("Failed to fetch"));

  assert.match(msg, /로컬 엔진 요청이 끊겼습니다/);
  assert.match(msg, /분석이 있으면 잠시 기다린 뒤 다시 시도/);
  assert.match(msg, /로컬 엔진 재시작|앱 재시작/);
  assert.doesNotMatch(msg, /라이선스 서버/);
});

test("control-server network error message stays separate from local-engine errors", () => {
  const msg = formatControlServerNetworkError(new TypeError("Failed to fetch"));

  assert.match(msg, /라이선스 서버/);
  assert.match(msg, /오프라인 모드/);
  assert.doesNotMatch(msg, /local-engine API/);
});

test("fetch network detection does not swallow HTTP detail errors", () => {
  assert.equal(isFetchNetworkError(new TypeError("Failed to fetch")), true);
  assert.equal(isFetchNetworkError(new Error("텍스트 분석 실패: ollama unavailable")), false);
  assert.equal(isFetchNetworkError(new Error("HTTP 502")), false);
});
