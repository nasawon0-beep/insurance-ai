export type CoverageRow = {
  name: string;
  status: "미가입" | "부족" | "충분" | string;
  pct: number;
  current: string | null;
  recommended: string | null;
  coverage_group?: string | null;
  coverage_name?: string | null;
  recommended_amount?: number | null;
  current_amount?: number | null;
  shortage_amount?: number | null;
  source_page?: number | null;
  raw_text?: string | null;
  confidence?: number | null;
};

// coverage_json(문자열) 파싱 결과. corrupt=true 는 "저장은 됐는데 읽을 수 없음" —
// json===null("내용 없음")과 구분해서 사용자에게 알려야 표가 왜 사라졌는지 안다.
export function parseCoverageJson(json: string | null): { rows: CoverageRow[]; corrupt: boolean } {
  if (!json) return { rows: [], corrupt: false };
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { rows: [], corrupt: true };
  }
  // 보장현황 표는 항상 배열. 배열이 아니면 구조적으로 손상된 것 —
  // 빈 표로 착각해 "표 만들기"로 덮어쓰지 않도록 corrupt 로 알린다.
  if (!Array.isArray(parsed)) return { rows: [], corrupt: true };
  return { rows: normalizeCoverageRows(parsed), corrupt: false };
}

export function normalizeCoverageRows(rows: unknown[]): CoverageRow[] {
  return (Array.isArray(rows) ? rows : [])
    .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
    .map((row) => {
      const name = String(row.name ?? row.coverage_name ?? "").trim();
      const status = String(row.status ?? "").trim();
      const pct = Number(row.pct);
      const current = typeof row.current === "string" ? row.current.trim() : "";
      const recommended = typeof row.recommended === "string" ? row.recommended.trim() : "";
      const recommendedAmount = Number(row.recommended_amount);
      const currentAmount = Number(row.current_amount);
      const shortageAmount = Number(row.shortage_amount);
      const sourcePage = Number(row.source_page);
      const confidence = Number(row.confidence);
      const normalized: CoverageRow = {
        name,
        status: status || "미가입",
        pct: Math.min(100, Math.max(0, Number.isFinite(pct) ? pct : 0)),
        current: current || null,
        recommended: recommended || null,
      };
      const coverageName = String(row.coverage_name ?? "").trim();
      const coverageGroup = typeof row.coverage_group === "string" ? row.coverage_group.trim() : "";
      if (coverageName) normalized.coverage_name = coverageName;
      if (coverageGroup) normalized.coverage_group = coverageGroup;
      if (Number.isFinite(recommendedAmount)) normalized.recommended_amount = recommendedAmount;
      if (Number.isFinite(currentAmount)) normalized.current_amount = currentAmount;
      if (Number.isFinite(shortageAmount)) normalized.shortage_amount = shortageAmount;
      if (Number.isFinite(sourcePage)) normalized.source_page = sourcePage;
      if (typeof row.raw_text === "string") normalized.raw_text = row.raw_text;
      if (Number.isFinite(confidence)) normalized.confidence = confidence;
      return normalized;
    })
    .filter((row) => !(row.name === "" && row.current == null && row.recommended == null));
}
