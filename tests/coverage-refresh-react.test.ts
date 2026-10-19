import assert from "node:assert/strict";
import test from "node:test";
import { refreshLatestCoverageAnalysis } from "../desktop/src/coverageRefresh.ts";

test("React refresh reloads latest revision before link-refresh instead of stale component state", async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  const api = async <T>(path: string, init?: RequestInit): Promise<T> => {
    calls.push({ path, init });
    if (!init) return { run_id: "observed-newest" } as T;
    return { run_id: "refreshed" } as T;
  };

  await refreshLatestCoverageAnalysis(api, "customer-1");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].path, "/customers/customer-1/coverage-analysis?audience=internal");
  assert.equal(calls[1].path, "/customers/customer-1/coverage-analysis/link-refresh");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    audience: "customer",
    run_id: "observed-newest",
  });
});

test("React refresh exposes server 409 when a revision appears between latest GET and POST", async () => {
  const calls: string[] = [];
  const api = async <T>(path: string, init?: RequestInit): Promise<T> => {
    calls.push(path);
    if (!init) return { run_id: "selected-by-get" } as T;
    throw new Error("HTTP 409: 최신 분석을 다시 불러온 뒤 재시도하세요.");
  };

  await assert.rejects(
    refreshLatestCoverageAnalysis(api, "customer-race"),
    /409.*최신 분석/,
  );
  assert.deepEqual(calls, [
    "/customers/customer-race/coverage-analysis?audience=internal",
    "/customers/customer-race/coverage-analysis/link-refresh",
  ]);
});
