export type CoverageApi = <T>(path: string, init?: RequestInit) => Promise<T>;

export type LatestCoverageRevision = {
  run_id: string;
};

export async function refreshLatestCoverageAnalysis<T>(
  api: CoverageApi,
  customerId: string,
): Promise<T> {
  const latest = await api<LatestCoverageRevision>(
    `/customers/${customerId}/coverage-analysis?audience=internal`,
  );
  if (!latest?.run_id) {
    throw new Error("최신 보장분석 revision을 확인할 수 없습니다.");
  }
  return api<T>(`/customers/${customerId}/coverage-analysis/link-refresh`, {
    method: "POST",
    body: JSON.stringify({ audience: "customer", run_id: latest.run_id }),
  });
}
