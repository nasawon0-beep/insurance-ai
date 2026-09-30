import assert from "node:assert/strict";
import test from "node:test";

import {
  formatControlServerNetworkError,
  formatLocalEngineNetworkError,
  isFetchNetworkError,
} from "../desktop/src/localEngineErrors.ts";

test("local-engine network error message names the local API and not the license server", () => {
  const msg = formatLocalEngineNetworkError(new TypeError("Failed to fetch"));

  assert.match(msg, /local-engine API/);
  assert.match(msg, /127\.0\.0\.1:8420/);
  assert.match(msg, /\/health/);
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
