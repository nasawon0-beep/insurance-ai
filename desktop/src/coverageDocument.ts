export type CaptureCoverageSource = {
  doc_type?: unknown;
  coverage_review?: { coverages?: unknown } | null;
  policy_coverages?: unknown;
};

export function normalizeCoverageDocumentType(value: unknown): string {
  const docType = String(value ?? "").trim().toLowerCase();
  if (["proposal", "가입제안서", "가입설계서", "상품제안서"].includes(docType)) return "proposal";
  if ([
    "coverage_analysis",
    "coverage_analysis_full",
    "coverage_analysis_detail_only",
    "coverage_analysis_short",
    "보장분석",
  ].includes(docType)) return "coverage_analysis";
  return docType;
}

export function isCoverageAnalysisDocument(value: unknown): boolean {
  return normalizeCoverageDocumentType(value) === "coverage_analysis";
}

export function isProposalDocument(value: unknown): boolean {
  return normalizeCoverageDocumentType(value) === "proposal";
}

export function isAggregateSummaryCoverage(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  const insurer = String(row.insurer ?? "").trim();
  const sourceType = String(row.source_type ?? "").trim().toLowerCase();
  return ["통합보장분석", "알 수 없음"].includes(insurer)
    || ["summary", "aggregate", "coverage_summary"].includes(sourceType);
}

export function isAggregateSummaryPolicy(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const policy = value as Record<string, unknown>;
  const insurer = String(policy.insurer ?? "").trim();
  const product = String(policy.product_name ?? "").replace(/\s+/g, "").replace(/[·-]/g, "").toLowerCase();
  if (insurer === "통합보장분석") return true;
  if (["m보장분석", "보장분석요약"].includes(product) && (!insurer || insurer === "알 수 없음")) return true;
  const coverages = Array.isArray(policy.policy_coverages) ? policy.policy_coverages : [];
  return coverages.length > 0 && coverages.every(isAggregateSummaryCoverage)
    && (!insurer || insurer === "알 수 없음");
}

export function describePolicySaveFailure(error: unknown, createdPolicy: boolean, savingCoverages: boolean): string {
  const detail = error instanceof Error ? error.message : String(error);
  return createdPolicy && savingCoverages
    ? `계약은 생성됐지만 담보 저장 실패: ${detail}`
    : detail;
}

export function selectReviewedPolicyCoverages(source: CaptureCoverageSource): Record<string, unknown>[] {
  const rows = Array.isArray(source.policy_coverages)
    ? source.policy_coverages
    : Array.isArray(source.coverage_review?.coverages)
      ? source.coverage_review.coverages
      : [];
  return rows.filter((item): item is Record<string, unknown> => {
    if (!item || typeof item !== "object") return false;
    const row = item as Record<string, unknown>;
    const insurer = String(row.insurer ?? "").trim();
    const rider = String(row.rider_name ?? row.coverage_name ?? row.standard_name ?? "").trim();
    // 50개 표준항목 집계표는 계약 원장이 아니다. 회사/상품 담보 상세만 저장한다.
    if (!insurer || isAggregateSummaryCoverage(row)) return false;
    return rider.length > 0;
  });
}
