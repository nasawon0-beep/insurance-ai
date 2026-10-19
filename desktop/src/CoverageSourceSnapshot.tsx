import { useState } from "react";
import CoverageBadge from "./components/CoverageBadge";
import { coverageAmountLabel } from "./coverageAmount";
import "./styles/coverage.css";

type AnalysisItem = {
  id: string;
  coverage_name: string;
  status: string;
  current_amount: number;
  amount_status?: string;
  amount_source_kind?: "observed_summary" | "calculated";
  observed_source_page?: number | null;
  calculated_current_amount?: number;
  calculated_amount_status?: string;
  source_difference?: string | null;
  matched_policy_coverage_ids?: string[];
};

type AnalysisCategory = {
  category_group: string;
  items: AnalysisItem[];
};

export const COVERAGE_CATEGORY_TABS = [
  "전체",
  "사망·후유장해",
  "암",
  "뇌혈관질환",
  "심장질환",
  "치매",
  "실손의료비",
  "수술비",
  "입원비(일당)·치료비",
  "운전자·법률배상·기타",
  "기타",
] as const;

export type CoverageCategoryTab = (typeof COVERAGE_CATEGORY_TABS)[number];

export function coverageCategoryTab(categoryGroup: string): CoverageCategoryTab {
  const mapping: Array<[string, CoverageCategoryTab]> = [
    ["뇌혈관질환", "뇌혈관질환"],
    ["심장질환", "심장질환"],
    ["실손의료비", "실손의료비"],
    ["후유장해", "사망·후유장해"],
    ["배상책임", "운전자·법률배상·기타"],
    ["사망", "사망·후유장해"],
    ["암", "암"],
    ["뇌", "뇌혈관질환"],
    ["심장", "심장질환"],
    ["치매", "치매"],
    ["실손", "실손의료비"],
    ["수술", "수술비"],
    ["입원비", "입원비(일당)·치료비"],
    ["일당", "입원비(일당)·치료비"],
    ["치료비", "입원비(일당)·치료비"],
    ["운전자", "운전자·법률배상·기타"],
    ["법률", "운전자·법률배상·기타"],
    ["배상", "운전자·법률배상·기타"],
    ["치아", "운전자·법률배상·기타"],
    ["화상", "운전자·법률배상·기타"],
    ["골절", "운전자·법률배상·기타"],
  ];
  return mapping.find(([key]) => categoryGroup.includes(key))?.[1] ?? "기타";
}

export function filterCoverageCategories(
  categories: AnalysisCategory[],
  activeTab: CoverageCategoryTab,
): AnalysisCategory[] {
  if (activeTab === "전체") return categories;
  return categories.filter((category) => coverageCategoryTab(category.category_group) === activeTab);
}

type AnalysisSnapshot = {
  run_id: string;
  categories: AnalysisCategory[];
  unclassified_policy_coverages?: PolicyCoverageSnapshot[];
  unlinked_policy_coverages?: PolicyCoverageSnapshot[];
  review_required_policy_coverages?: PolicyCoverageSnapshot[];
  source_link_occurrences?: SourceLinkOccurrence[];
  unresolved_source_link_occurrences?: SourceLinkOccurrence[];
  held_source_link_occurrences?: SourceLinkOccurrence[];
};

export type SourceLinkOccurrence = {
  id: string;
  source_hash?: string | null;
  source_page: number;
  source_table: number;
  source_row: number;
  source_standard_name_raw?: string | null;
  source_standard_name?: string | null;
  insurer?: string | null;
  product_name?: string | null;
  rider_name?: string | null;
  amount?: number | null;
  amount_text?: string | null;
  resolution_status: string;
  resolution_reason: string;
  candidate_policy_coverage_ids?: string[];
};

export type PolicyCoverageSnapshot = {
  id: string;
  insurer?: string | null;
  product_name?: string | null;
  rider_name?: string | null;
  rider_name_raw?: string | null;
  standard_name?: string | null;
  standard_name_raw?: string | null;
  standard_name_extracted_raw?: string | null;
  standard_name_verification?: string | null;
  amount?: number | null;
  amount_text?: string | null;
  amount_status?: string | null;
  source_page?: number | null;
};

const UNCERTAIN_AMOUNT_STATUSES = new Set([
  "unknown",
  "partial_unknown",
  "unknown_dash",
  "unknown_blank",
  "parse_failed",
]);

export function customerCoverageStatus(item: AnalysisItem): "가입" | "미가입" | "확인필요" {
  if (UNCERTAIN_AMOUNT_STATUSES.has(item.amount_status ?? "")) return "확인필요";
  return Number(item.current_amount ?? 0) > 0 ? "가입" : "미가입";
}

export function koreanCoverageAmount(
  amount: number | null | undefined,
  amountStatus?: string | null,
  amountText?: string | null,
): string {
  const exact = coverageAmountLabel(amount, amountStatus, amountText);
  if (exact === "금액 확인필요" || exact === "0원 (원문 명시)") return exact;
  if (typeof amount !== "number" || !Number.isFinite(amount)) return exact;
  if (amount === 0) return "0원";

  const eok = Math.floor(amount / 100_000_000);
  const remainder = amount % 100_000_000;
  const man = Math.floor(remainder / 10_000);
  const won = remainder % 10_000;
  const parts: string[] = [];
  if (eok) parts.push(`${eok.toLocaleString("ko-KR")}억`);
  if (man) parts.push(`${man.toLocaleString("ko-KR")}만`);
  if (won) parts.push(won.toLocaleString("ko-KR"));
  return `${parts.join(" ")}원`;
}

function CoverageDetail({ detail }: { detail: PolicyCoverageSnapshot }) {
  return (
    <div className="coverage-ledger-detail" data-detail-id={detail.id}>
      <div className="coverage-ledger-company">{detail.insurer || "보험사 미확인"}</div>
      {detail.product_name && <div className="coverage-ledger-product">{detail.product_name}</div>}
      <div className="coverage-ledger-rider">
        {detail.rider_name_raw || detail.rider_name || detail.standard_name_raw || detail.standard_name || "담보명 확인필요"}
      </div>
      {detail.standard_name && (
        <div className="coverage-ledger-standard">
          표준분류: {detail.standard_name}
          {detail.standard_name_raw && detail.standard_name_raw !== detail.standard_name
            ? ` (검증 원문: ${detail.standard_name_raw})`
            : ""}
        </div>
      )}
      {detail.standard_name_extracted_raw && detail.standard_name_extracted_raw !== detail.standard_name_raw && (
        <div className="coverage-ledger-extracted">
          셀 추출 원문: {detail.standard_name_extracted_raw}
        </div>
      )}
      {detail.standard_name_verification === "needs_review" && (
        <div className="coverage-ledger-review">신정원 원본분류 검수 필요</div>
      )}
      <div className="coverage-ledger-amount">
        {koreanCoverageAmount(detail.amount, detail.amount_status, detail.amount_text)}
      </div>
    </div>
  );
}

export default function CoverageSourceSnapshot({
  analysis,
  policyCoverages,
  onReviewOccurrence,
}: {
  analysis: AnalysisSnapshot;
  policyCoverages: PolicyCoverageSnapshot[];
  title?: string;
  onReviewOccurrence?: (occurrenceId: string, action: "link" | "hold", policyCoverageId?: string) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = useState<CoverageCategoryTab>("전체");
  const [reviewSelections, setReviewSelections] = useState<Record<string, string>>({});
  const [reviewBusy, setReviewBusy] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const byId = new Map(policyCoverages.map((row) => [row.id, row]));
  const visibleCategories = filterCoverageCategories(analysis.categories, activeTab);
  const unlinked = analysis.unlinked_policy_coverages ?? analysis.unclassified_policy_coverages ?? [];
  const reviewRequired = analysis.review_required_policy_coverages ?? [];
  const unresolvedOccurrences = analysis.unresolved_source_link_occurrences ?? [];
  const heldOccurrences = analysis.held_source_link_occurrences ?? [];

  const reviewOccurrence = async (occurrence: SourceLinkOccurrence, action: "link" | "hold") => {
    if (!onReviewOccurrence) return;
    const selected = reviewSelections[occurrence.id];
    if (action === "link" && !selected) {
      setReviewError("연결할 계약담보를 선택해 주세요.");
      return;
    }
    setReviewBusy(occurrence.id);
    setReviewError(null);
    try {
      await onReviewOccurrence(occurrence.id, action, selected);
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : String(error));
    } finally {
      setReviewBusy(null);
    }
  };

  return (
    <section className="coverage-user-analysis" data-run-id={analysis.run_id} aria-label="보장분석 결과">
      <div className="coverage-category-tabs" role="tablist" aria-label="보장 카테고리 선택">
        {COVERAGE_CATEGORY_TABS.map((category) => (
          <button
            type="button"
            role="tab"
            key={category}
            className={`coverage-tab ${activeTab === category ? "is-active" : ""}`}
            aria-selected={activeTab === category}
            tabIndex={activeTab === category ? 0 : -1}
            onClick={() => setActiveTab(category)}
          >
            {category}
          </button>
        ))}
      </div>
      <div className="coverage-category-panel" role="tabpanel" aria-label={`${activeTab} 보장 항목`}>
      {visibleCategories.length === 0 && (
        <p className="coverage-category-empty">해당 카테고리에 표시할 보장 항목이 없습니다.</p>
      )}
      {visibleCategories.map((category, categoryIndex) => (
        <section className="coverage-user-category" key={`${category.category_group}-${categoryIndex}`}>
          <h3>{category.category_group}</h3>
          <div className="coverage-user-items">
            {category.items.map((item, itemIndex) => {
              const detailIds = item.matched_policy_coverage_ids ?? [];
              const status = customerCoverageStatus(item);
              return (
                <details
                  className="coverage-user-item"
                  key={`${item.id}-${itemIndex}`}
                  data-item-id={item.id}
                  open={status !== "미가입"}
                >
                  <summary>
                    <span className="coverage-user-item-name">{item.coverage_name}</span>
                    <CoverageBadge status={status} />
                    <span className="coverage-user-item-amount">
                      {koreanCoverageAmount(item.current_amount, item.amount_status)}
                    </span>
                  </summary>
                  <div className="coverage-user-details">
                    <p className="coverage-source-kind">
                      {item.amount_source_kind === "observed_summary"
                        ? `원본 요약${item.observed_source_page ? ` · ${item.observed_source_page}쪽` : ""}`
                        : "원본 요약 없음 · 계약담보 자체계산"}
                    </p>
                    {item.amount_source_kind === "observed_summary" && item.source_difference && (
                      <p className="coverage-source-difference">
                        계약담보 자체계산: {koreanCoverageAmount(item.calculated_current_amount, item.calculated_amount_status)} · 원본과 다름
                      </p>
                    )}
                    {detailIds.length === 0 ? (
                      <p className="coverage-user-no-detail">
                        {status === "미가입" ? "가입된 회사상품담보가 없습니다." : "연결된 회사상품담보를 확인해 주세요."}
                      </p>
                    ) : (
                      detailIds.map((id, detailIndex) => {
                        const detail = byId.get(id);
                        return detail ? (
                          <CoverageDetail key={`${id}-${detailIndex}`} detail={detail} />
                        ) : (
                          <p className="coverage-user-no-detail" data-detail-id={id} key={`${id}-${detailIndex}`}>
                            회사상품담보 상세 조회가 필요합니다.
                          </p>
                        );
                      })
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        </section>
      ))}
      </div>

      {unlinked.length > 0 && (
        <details className="coverage-unclassified" data-testid="coverage-unclassified">
          <summary>표준담보 미연결 원장 ({unlinked.length})</summary>
          <p className="coverage-unclassified-help">
            표준담보에 임의로 연결하지 않은 원문 담보입니다. 연결 여부와 검수 필요 여부는 별도입니다.
          </p>
          <div className="coverage-unclassified-list">
            {unlinked.map((row, index) => (
              <div className="coverage-unclassified-row" key={`${row.id}-${index}`} data-detail-id={row.id}>
                <div>
                  <strong>{row.rider_name_raw || row.rider_name || row.standard_name_raw || row.standard_name || "원문 담보명 확인필요"}</strong>
                  {(row.insurer || row.product_name) && (
                    <small>{[row.insurer, row.product_name].filter(Boolean).join(" · ")}</small>
                  )}
                </div>
                <span>{koreanCoverageAmount(row.amount, row.amount_status, row.amount_text)}</span>
                <span className="coverage-unclassified-source">
                  {row.source_page ? `원본 ${row.source_page}쪽` : "원본 위치 확인필요"}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
      {reviewRequired.length > 0 && (
        <details className="coverage-unclassified" data-testid="coverage-review-required">
          <summary>검수 필요 원장 ({reviewRequired.length})</summary>
          <p className="coverage-unclassified-help">미연결 여부와 독립적으로 원문분류·금액·연결 상태를 확인해야 하는 원장입니다.</p>
          <div className="coverage-unclassified-list">
            {reviewRequired.map((row, index) => (
              <div className="coverage-unclassified-row" key={`${row.id}-${index}`} data-detail-id={row.id}>
                <div>
                  <strong>{row.rider_name_raw || row.rider_name || "원문 담보명 확인필요"}</strong>
                  <small>
                    {row.standard_name_verification === "needs_review"
                      ? "신정원 원본분류 geometry 검증 불확실"
                      : "연결 또는 금액 상태 검수 필요"}
                    {row.source_page ? ` · 원본 ${row.source_page}쪽` : " · 원본 위치 확인필요"}
                  </small>
                  {row.standard_name_extracted_raw && (
                    <small>셀 추출: {row.standard_name_extracted_raw} · 검증 원문: {row.standard_name_raw || "확인필요"}</small>
                  )}
                </div>
                <span>{koreanCoverageAmount(row.amount, row.amount_status, row.amount_text)}</span>
              </div>
            ))}
          </div>
        </details>
      )}
      {unresolvedOccurrences.length > 0 && (
        <details className="coverage-source-review" data-testid="coverage-source-occurrence-review">
          <summary>원본 상세연결 검수 ({unresolvedOccurrences.length})</summary>
          <p className="coverage-unclassified-help">
            자동으로 확정하지 못한 원본 상세행입니다. 원본 위치와 불일치 사유를 확인한 뒤 직접 연결하거나 보류하세요.
          </p>
          {reviewError && <p className="coverage-error">검수 실패: {reviewError}</p>}
          <div className="coverage-unclassified-list">
            {unresolvedOccurrences.map((occurrence) => {
              const candidateIds = new Set(occurrence.candidate_policy_coverage_ids ?? []);
              const candidateRows = policyCoverages.filter((row) => candidateIds.has(row.id));
              return (
              <div className="coverage-source-occurrence" key={occurrence.id}>
                <div>
                  <strong>{occurrence.source_standard_name_raw || occurrence.source_standard_name || "원본 분류 확인필요"}</strong>
                  <small>{[occurrence.insurer, occurrence.product_name, occurrence.rider_name].filter(Boolean).join(" · ")}</small>
                  <small>
                    원본 {occurrence.source_page}쪽 · 표 {occurrence.source_table + 1} · 행 {occurrence.source_row + 1}
                    {` · 사유: ${occurrence.resolution_reason}`}
                    {` · 후보 ${(occurrence.candidate_policy_coverage_ids ?? []).length}건`}
                  </small>
                </div>
                <span>{koreanCoverageAmount(occurrence.amount, occurrence.amount == null ? "no_evidence" : "known", occurrence.amount_text)}</span>
                {onReviewOccurrence && (
                  <div className="coverage-source-review-actions">
                    {candidateRows.length > 0 ? (
                      <>
                        <select
                          aria-label="연결할 후보 계약담보 선택"
                          value={reviewSelections[occurrence.id] ?? ""}
                          onChange={(event) => setReviewSelections((current) => ({ ...current, [occurrence.id]: event.target.value }))}
                        >
                          <option value="">후보 계약담보 선택</option>
                          {candidateRows.map((row) => (
                            <option key={row.id} value={row.id}>
                              {[row.insurer, row.product_name, row.rider_name_raw || row.rider_name].filter(Boolean).join(" · ")}
                            </option>
                          ))}
                        </select>
                        <button type="button" disabled={reviewBusy === occurrence.id} onClick={() => void reviewOccurrence(occurrence, "link")}>선택 연결</button>
                      </>
                    ) : (
                      <small>{candidateIds.size > 0 ? "후보 원장이 변경되어 다시 분석이 필요합니다." : "연결 후보가 없어 보류만 가능합니다."}</small>
                    )}
                    <button type="button" disabled={reviewBusy === occurrence.id} onClick={() => void reviewOccurrence(occurrence, "hold")}>보류</button>
                  </div>
                )}
              </div>
              );
            })}
          </div>
        </details>
      )}
      {heldOccurrences.length > 0 && (
        <details className="coverage-source-review" data-testid="coverage-source-occurrence-held" open>
          <summary>보류/미확정 ({heldOccurrences.length})</summary>
          <p className="coverage-unclassified-help">
            확정 연결 통계에 포함되지 않습니다. 다시 검수할 때까지 원본 위치와 보류 사유를 유지합니다.
          </p>
          <div className="coverage-unclassified-list">
            {heldOccurrences.map((occurrence) => (
              <div className="coverage-source-occurrence" key={occurrence.id}>
                <div>
                  <strong>{occurrence.source_standard_name_raw || occurrence.source_standard_name || "원본 분류 확인필요"}</strong>
                  <small>{[occurrence.insurer, occurrence.product_name, occurrence.rider_name].filter(Boolean).join(" · ")}</small>
                  <small>
                    원본 {occurrence.source_page}쪽 · 표 {occurrence.source_table + 1} · 행 {occurrence.source_row + 1}
                    {` · 보류 사유: ${occurrence.resolution_reason}`}
                  </small>
                </div>
                <span>{koreanCoverageAmount(occurrence.amount, occurrence.amount == null ? "no_evidence" : "known", occurrence.amount_text)}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
