import { useCallback, useEffect, useMemo, useState } from "react";
import CoverageBadge from "./components/CoverageBadge";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";
import { formatLocalEngineNetworkError, isFetchNetworkError } from "./localEngineErrors";
import "./styles/coverage.css";

type CustomerOption = {
  id: string;
  customer_id?: string;
  name: string;
  phone?: string | null;
  birth_date?: string | null;
};

type CoverageRunOption = {
  id?: string;
  run_id?: string;
  customer_id?: string;
  customer_name?: string;
  name?: string;
  phone?: string | null;
  customer_phone?: string | null;
  birth_date?: string | null;
  customer_birth_date?: string | null;
  customer?: Partial<CustomerOption> | null;
};


type CoverageItem = {
  id: string;
  source_index: number;
  source_kind?: "analysis" | "policy";
  category_id?: string;
  category_group: string;
  coverage_name: string;
  status: string;
  current_text: string | null;
  recommended_text: string | null;
  recommended_amount: number;
  current_amount: number;
  gap_amount: number;
  priority?: string;
  priority_score?: number;
  customer_summary?: string;
  internal_memo?: string;
  evidence_pages?: number[];
};

type PolicyCoverage = {
  id: string;
  source_page?: number | null;
  insurer?: string | null;
  product_name?: string | null;
  coverage_type?: string | null;
  rider_name?: string | null;
  standard_name?: string | null;
  amount?: number | null;
  amount_text?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

type CoverageCategory = {
  category_id: string;
  category_group: string;
  status_summary: string;
  max_priority: string;
  items: CoverageItem[];
};

type CoverageResponse = {
  customer_id: string;
  run_id: string;
  status: string;
  doc_type?: "coverage_analysis";
  audience: "customer";
  summary?: {
    overall?: string;
    total_items?: number;
    displayed_items?: number;
    counts?: Record<string, number>;
    priority_counts?: Record<string, number>;
  };
  categories: CoverageCategory[];
};


type CoverageRefreshResponse = {
  status?: string;
  doc_type?: string;
  items_count?: number;
  links?: { result?: string };
};

type PolicyCoveragesResponse = {
  policy_coverages?: PolicyCoverage[];
};

type EditDraft = {
  status: string;
  current: string;
  recommended: string;
};

type CoverageFilter = "전체" | "부족" | "미가입" | "충분" | "암" | "뇌혈관" | "심장" | "수술비" | "입원비" | "운전자" | "기타";

const RECENT_CUSTOMERS_KEY = "coverageAnalysis.recentCustomerIds";
const DEBUG_PREFIX = "[CoverageAnalysis]";
const COVERAGE_FILTERS: CoverageFilter[] = ["전체", "부족", "미가입", "충분", "암", "뇌혈관", "심장", "수술비", "입원비", "운전자", "기타"];

function parseWonAmount(value: string | null | undefined): number {
  if (!value) return 0;
  const text = value.replace(/,/g, "").trim();
  const sign = text.startsWith("-") ? -1 : 1;
  const eokMatch = text.match(/(\d+(?:\.\d+)?)\s*억/);
  const manMatch = text.match(/(\d+(?:\.\d+)?)\s*만/);
  if (eokMatch || manMatch) {
    return sign * Math.round(Number(eokMatch?.[1] ?? 0) * 100000000 + Number(manMatch?.[1] ?? 0) * 10000);
  }
  const plain = text.match(/\d+(?:\.\d+)?/);
  return plain ? sign * Math.round(Number(plain[0])) : 0;
}

function statusIcon(status: string): string {
  if (status === "충분") return "✓";
  if (status === "미가입") return "—";
  return "";
}

function policyCoverageGroup(coverage: PolicyCoverage): string {
  const text = `${coverage.standard_name ?? ""} ${coverage.rider_name ?? ""} ${coverage.coverage_type ?? ""}`;
  if (/암/.test(text)) return "암";
  if (/뇌|뇌혈관|뇌졸중|뇌출혈/.test(text)) return "뇌혈관질환";
  if (/심장|허혈|급성심근|협심/.test(text)) return "심장질환";
  if (/수술/.test(text)) return "수술비";
  if (/입원|일당/.test(text)) return "입원비/일당";
  if (/운전자|교통|벌금|변호사|방어비용/.test(text)) return "운전자";
  return coverage.coverage_type || "기타";
}

function policyCoverageToItem(coverage: PolicyCoverage, index: number): CoverageItem {
  const coverageName = coverage.rider_name || coverage.standard_name || "담보명 미확인";
  const currentAmount = Number(coverage.amount ?? 0);
  return {
    id: `policy-${coverage.id || index}`,
    source_index: index,
    source_kind: "policy",
    category_group: policyCoverageGroup(coverage),
    coverage_name: coverageName,
    status: "보유",
    current_text: coverage.amount_text ?? null,
    recommended_text: null,
    recommended_amount: 0,
    current_amount: Number.isFinite(currentAmount) ? currentAmount : 0,
    gap_amount: 0,
    customer_summary: [coverage.insurer, coverage.product_name].filter(Boolean).join(" · ") || undefined,
    internal_memo: [coverage.start_date, coverage.end_date].filter(Boolean).join(" ~ ") || undefined,
    evidence_pages: coverage.source_page ? [coverage.source_page] : undefined,
  };
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    console.log(`${DEBUG_PREFIX} api:request`, { method: init?.method ?? "GET", path });
    // Customers.tsx와 동일하게 engineFetch를 통해 호출한다.
    // engineFetch가 X-Insurance-AI-Secret / X-Actor-Id 인증 헤더를 주입한다.
    res = await engineFetch(path, {
      headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...init,
    });
    console.log(`${DEBUG_PREFIX} api:response`, { method: init?.method ?? "GET", path, status: res.status, ok: res.ok });
  } catch (e) {
    if (isFetchNetworkError(e)) {
      throw new Error(formatLocalEngineNetworkError(e));
    }
    throw e;
  }
  if (!res.ok) {
    const bodyText = await res.text().catch(() => "");
    let body: { detail?: unknown } | null = null;
    try {
      body = bodyText ? JSON.parse(bodyText) : null;
    } catch {
      body = null;
    }
    const detail = formatErrorDetail(body?.detail) ?? bodyText.trim();
    throw new Error(`${detail ? `${detail} ` : ""}(HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}, ${path})`);
  }
  return (res.status === 204 ? null : await res.json()) as T;
}

function formatAmount(value: number | null | undefined): string {
  const manwon = Math.round(Number(value || 0) / 10000);
  return `${manwon.toLocaleString("ko-KR")}만원`;
}

function countByStatus(items: CoverageItem[], status: string): number {
  return items.filter((item) => item.status === status).length;
}

function filterMatches(item: CoverageItem, filter: CoverageFilter): boolean {
  if (filter === "전체") return true;
  if (["부족", "미가입", "충분"].includes(filter)) return item.status === filter;
  const groupByFilter: Partial<Record<CoverageFilter, string[]>> = {
    암: ["암"],
    뇌혈관: ["뇌혈관질환"],
    심장: ["심장질환"],
    수술비: ["수술비"],
    입원비: ["입원비/일당", "입원비 / 일당"],
    운전자: ["운전자"],
  };
  const groups = groupByFilter[filter];
  if (groups) return groups.includes(item.category_group);
  const knownGroups = Object.values(groupByFilter).flat();
  return !knownGroups.includes(item.category_group);
}

function normalizeRunCustomers(body: unknown): CustomerOption[] {
  const source = body as {
    runs?: CoverageRunOption[];
    items?: CoverageRunOption[];
    data?: CoverageRunOption[];
    customers?: CustomerOption[];
  } | CoverageRunOption[] | null;

  if (!source) return [];

  const runs = Array.isArray(source)
    ? source
    : Array.isArray(source.runs)
      ? source.runs
      : Array.isArray(source.items)
        ? source.items
        : Array.isArray(source.data)
          ? source.data
          : Array.isArray(source.customers)
            ? source.customers
            : [];

  const byCustomerId = new Map<string, CustomerOption>();
  for (const raw of runs) {
    const run = raw as CoverageRunOption & CustomerOption;
    const customer = run.customer ?? null;
    // /coverage-analysis-runs 의 id/run_id 는 분석 run ID다.
    // 고객 상세 API에는 반드시 runs.customer_id 를 우선 사용해야 한다.
    const id = String(run.customer_id ?? customer?.customer_id ?? customer?.id ?? run.id ?? "").trim();
    const name = String(customer?.name ?? run.customer_name ?? run.name ?? "").trim();
    if (!id || !name || byCustomerId.has(id)) continue;
    byCustomerId.set(id, {
      id,
      customer_id: id,
      name,
      phone: customer?.phone ?? run.customer_phone ?? run.phone ?? null,
      birth_date: customer?.birth_date ?? run.customer_birth_date ?? run.birth_date ?? null,
    });
  }
  return [...byCustomerId.values()];
}

export default function CoverageAnalysisScreen() {
  const [customers, setCustomers] = useState<CustomerOption[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerSearchOpen, setCustomerSearchOpen] = useState(false);
  const [recentCustomerIds, setRecentCustomerIds] = useState<string[]>([]);
  const [data, setData] = useState<CoverageResponse | null>(null);
  const [policyCoverages, setPolicyCoverages] = useState<PolicyCoverage[]>([]);
  const [editingRowId, setEditingRowId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({ status: "미가입", current: "", recommended: "" });
  const [savingRowId, setSavingRowId] = useState<string | null>(null);
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("전체");
  const [showAll, setShowAll] = useState(false);
  const [customersBusy, setCustomersBusy] = useState(false);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedCustomer = useMemo(
    () => customers.find((customer) => customer.id === customerId) ?? null,
    [customers, customerId],
  );

  const selectCustomer = useCallback((customer: CustomerOption) => {
    setCustomerId(customer.id);
    setCustomerQuery(customer.name);
    setCustomerSearchOpen(false);
    setRecentCustomerIds((current) => {
      const next = [customer.id, ...current.filter((id) => id !== customer.id)].slice(0, 5);
      window.localStorage.setItem(RECENT_CUSTOMERS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const loadCustomers = useCallback(async (options?: { rethrow?: boolean }) => {
    setCustomersBusy(true);
    setError(null);
    try {
      const body = await api<unknown>("/coverage-analysis-runs");
      const list = normalizeRunCustomers(body);
      setCustomers(list);
      setCustomerId((current) => (current && list.some((customer) => customer.id === current) ? current : ""));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (options?.rethrow) throw e;
    } finally {
      setCustomersBusy(false);
    }
  }, []);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(RECENT_CUSTOMERS_KEY) ?? "[]");
      if (Array.isArray(saved)) setRecentCustomerIds(saved.filter((id): id is string => typeof id === "string").slice(0, 5));
    } catch {
      setRecentCustomerIds([]);
    }
  }, []);

  const loadAnalysis = useCallback(async () => {
    if (!customerId) {
      setData(null);
      setPolicyCoverages([]);
      setEditingRowId(null);
      return;
    }
    setAnalysisBusy(true);
    setError(null);
    setRefreshMessage(null);
    try {
      const [analysis, coverages] = await Promise.all([
        api<CoverageResponse>(`/customers/${customerId}/coverage-analysis?audience=customer`),
        api<PolicyCoveragesResponse>(`/customers/${customerId}/policy-coverages`),
      ]);
      setData(analysis);
      setPolicyCoverages(Array.isArray(coverages.policy_coverages) ? coverages.policy_coverages : []);
      setEditingRowId(null);
    } catch (e) {
      setData(null);
      setPolicyCoverages([]);
      setEditingRowId(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalysisBusy(false);
    }
  }, [customerId]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    setRefreshMessage(null);
    try {
      await loadCustomers({ rethrow: true });

      if (customerId) {
        const result = await api<CoverageRefreshResponse>(`/customers/${customerId}/coverage-analysis/recalculate`, {
          method: "POST",
          body: JSON.stringify({ audience: "customer", force: true, source: "coverage_analysis_refresh" }),
        });
        const docType = result.doc_type ?? "coverage_analysis";
        if (docType === "proposal") {
          setRefreshMessage("가입제안서는 신규 제안 검토용이라 보유담보에 저장하지 않았습니다.");
          setData(null);
          return;
        }
        await loadAnalysis();
        setRefreshMessage(`보장분석서 재계산 완료${result.items_count != null ? ` (${result.items_count}개 항목)` : ""}`);
      } else {
        setRefreshMessage("고객 목록을 새로 조회했습니다. 재계산할 고객을 선택하세요.");
      }
    } catch (e) {
      console.error("새로고침 실패:", e);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }, [customerId, loadAnalysis, loadCustomers]);

  useEffect(() => {
    void loadCustomers();
  }, [loadCustomers]);

  useEffect(() => {
    void loadAnalysis();
  }, [loadAnalysis]);

  useEffect(() => {
    if (selectedCustomer) setCustomerQuery(selectedCustomer.name);
  }, [selectedCustomer]);

  useEffect(() => {
    if (!data || !customerId) return;
    setRecentCustomerIds((current) => {
      const next = [customerId, ...current.filter((id) => id !== customerId)].slice(0, 5);
      window.localStorage.setItem(RECENT_CUSTOMERS_KEY, JSON.stringify(next));
      return next;
    });
  }, [customerId, data]);

  const analysisItems = useMemo(
    () => (data?.categories ?? []).flatMap((category) => category.items.map((item) => ({ ...item, source_kind: "analysis" as const }))),
    [data],
  );
  const policyItems = useMemo(() => policyCoverages.map(policyCoverageToItem), [policyCoverages]);
  const allItems = showAll ? policyItems : analysisItems;
  const filteredItems = useMemo(
    () => allItems.filter((item) => filterMatches(item, coverageFilter)),
    [allItems, coverageFilter],
  );

  const customerSuggestions = useMemo(() => {
    const normalizedQuery = customerQuery.trim().toLowerCase();
    const recentCustomers = recentCustomerIds
      .map((id) => customers.find((customer) => customer.id === id))
      .filter((customer): customer is CustomerOption => !!customer);

    const matches = normalizedQuery
      ? customers.filter((customer) => {
          const phone = customer.phone ?? "";
          return customer.name.toLowerCase().includes(normalizedQuery) || phone.toLowerCase().includes(normalizedQuery);
        })
      : customers;

    const ordered = [
      ...recentCustomers.filter((customer) => matches.some((match) => match.id === customer.id)),
      ...matches.filter((customer) => !recentCustomers.some((recent) => recent.id === customer.id)),
    ];

    return ordered.slice(0, 10);
  }, [customerQuery, customers, recentCustomerIds]);

  const shortageTotal = countByStatus(analysisItems, "부족") + countByStatus(analysisItems, "미가입");

  const startEditingRow = (item: CoverageItem) => {
    setEditingRowId(item.id);
    setEditDraft({
      status: item.status || "미가입",
      current: item.current_text ?? "",
      recommended: item.recommended_text ?? "",
    });
    setError(null);
  };

  const cancelEditingRow = () => {
    setEditingRowId(null);
    setSavingRowId(null);
    setEditDraft({ status: "미가입", current: "", recommended: "" });
  };

  const saveEditingRow = async (item: CoverageItem) => {
    if (!customerId) {
      setError("고객을 먼저 선택하세요.");
      return;
    }
    setSavingRowId(item.id);
    setError(null);
    try {
      await api(`/customers/${customerId}/coverage-analysis/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          status: editDraft.status,
          current_amount: parseWonAmount(editDraft.current),
          recommended_amount: parseWonAmount(editDraft.recommended),
          override_reason: "보장분석 탭 수동 수정",
        }),
      });
      setEditingRowId(null);
      await loadAnalysis();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingRowId(null);
    }
  };

  return (
    <main className="coverage-page">
      <header className="coverage-header">
        <div>
          <h2 className="coverage-title">보장분석</h2>
          <p className="coverage-subtitle">보장분석서(coverage_analysis) 기준으로 재계산합니다. 가입제안서(proposal) riders는 검토만 하고 보유담보로 저장하지 않습니다.</p>
        </div>
        <button type="button" className="coverage-button" onClick={() => void handleRefresh()} disabled={refreshing}>
          {refreshing ? "새로고침 중…" : "🔄 새로고침"}
        </button>
      </header>

      <section className="coverage-toolbar" aria-label="보장분석 조건">
        <div className="coverage-field">
          <div className="coverage-field-header">
            <label htmlFor="coverage-customer">고객 선택</label>
          </div>
          <div className="coverage-customer-search">
            <input
              id="coverage-customer"
              className="coverage-search-input"
              type="search"
              value={customerQuery}
              placeholder={customersBusy ? "고객 불러오는 중…" : "고객을 검색하세요..."}
              autoComplete="off"
              disabled={customersBusy || customers.length === 0}
              onChange={(e) => {
                setCustomerQuery(e.target.value);
                setCustomerSearchOpen(true);
              }}
              onFocus={() => {
                if (selectedCustomer && customerQuery === selectedCustomer.name) setCustomerQuery("");
                setCustomerSearchOpen(true);
              }}
              onBlur={() => {
                window.setTimeout(() => {
                  setCustomerSearchOpen(false);
                  setCustomerQuery((current) => current || selectedCustomer?.name || "");
                }, 120);
              }}
            />
            {customerSearchOpen && !customersBusy && (
              <div className="coverage-suggestions" role="listbox" aria-label="고객 검색 결과">
                {customerSuggestions.length === 0 ? (
                  <div className="coverage-suggestion-empty">검색 결과가 없습니다</div>
                ) : (
                  customerSuggestions.map((customer) => {
                    const isRecent = recentCustomerIds.includes(customer.id);
                    return (
                      <button
                        key={customer.id}
                        type="button"
                        className={`coverage-suggestion ${customer.id === customerId ? "is-selected" : ""}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => selectCustomer(customer)}
                        role="option"
                        aria-selected={customer.id === customerId}
                      >
                        <span>
                          <strong>{customer.name}</strong>
                          {customer.phone ? <small>{customer.phone}</small> : null}
                        </span>
                        {isRecent && <em>최근</em>}
                      </button>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>

        <div className="coverage-actions">
          <span className="coverage-edit-guide">각 행의 수정 버튼으로 현재 보장, 권장 금액, 상태를 직접 수정할 수 있습니다.</span>
        </div>
      </section>

      {data && (
        <section className="coverage-summary" aria-label="전체현황">
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">선택 고객</p>
            <p className="coverage-summary-value">{selectedCustomer?.name ?? "-"}</p>
          </div>
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">부족/미가입</p>
            <p className="coverage-summary-value">{shortageTotal}개</p>
          </div>
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">표시/미가입/부족/충분</p>
            <p className="coverage-summary-value">
              {allItems.length} / {countByStatus(allItems, "미가입")} / {countByStatus(allItems, "부족")} / {countByStatus(allItems, "충분")}
            </p>
          </div>
        </section>
      )}

      {error && <div className="coverage-error">오류: {error}</div>}
      {!error && refreshMessage && <div className="coverage-empty">{refreshMessage}</div>}
      {!error && !data && customerId && (
        <div className="coverage-empty">
          {analysisBusy ? "보장분석 결과를 불러오는 중…" : "저장된 보장분석 데이터가 없습니다. 고객 목록에서 수정하세요."}
        </div>
      )}

      {data && (
        <section className="coverage-analysis-panel" aria-label="보장분석 상세표">
          <div className="coverage-filter-row" role="group" aria-label="보장분석 필터">
            <label className="coverage-show-all-toggle">
              <input
                type="checkbox"
                checked={showAll}
                onChange={(e) => {
                  setShowAll(e.target.checked);
                  setCoverageFilter("전체");
                }}
              />
              전체 보기 (중복 포함 {policyCoverages.length}개)
            </label>
            {COVERAGE_FILTERS.map((filter) => (
              <button
                key={filter}
                type="button"
                className={`coverage-filter ${coverageFilter === filter ? "is-active" : ""}`}
                onClick={() => setCoverageFilter(filter)}
              >
                {filter}
              </button>
            ))}
          </div>
          <div className="coverage-table-card" role="tabpanel">
            <div className="coverage-table-heading">
              <h3>{showAll ? "계약별 담보 원장 전체" : "보장별 상세표"}</h3>
              <span>{coverageFilter} {filteredItems.length}개</span>
            </div>

            {filteredItems.length === 0 ? (
              <div className="coverage-empty in-panel">표시할 세부 담보가 없습니다.</div>
            ) : (
              <div className="coverage-table-wrap">
                <table className="coverage-table">
                  <thead>
                    <tr>
                      <th>보장군</th>
                      <th>보장상세</th>
                      <th>권장금액</th>
                      <th>가입금액</th>
                      <th>부족금액</th>
                      <th>상태</th>
                      <th>관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredItems.map((item) => {
                      const editing = editingRowId === item.id;
                      const saving = savingRowId === item.id;
                      const isPolicyCoverage = item.source_kind === "policy";
                      const icon = statusIcon(item.status);
                      return (
                        <tr key={item.id}>
                          <td>{item.category_group}</td>
                          <td>
                            <div className="coverage-table-title">{item.coverage_name}</div>
                            {item.customer_summary ? <p>{item.customer_summary}</p> : null}
                            {item.internal_memo ? <p>{item.internal_memo}</p> : null}
                          </td>
                          <td>
                            {editing ? (
                              <input
                                className="coverage-edit-input"
                                type="text"
                                value={editDraft.recommended}
                                disabled={saving}
                                onChange={(e) => setEditDraft((current) => ({ ...current, recommended: e.target.value }))}
                              />
                            ) : isPolicyCoverage ? (
                              "-"
                            ) : (
                              item.recommended_text || formatAmount(item.recommended_amount)
                            )}
                          </td>
                          <td>
                            {editing ? (
                              <input
                                className="coverage-edit-input"
                                type="text"
                                value={editDraft.current}
                                disabled={saving}
                                onChange={(e) => setEditDraft((current) => ({ ...current, current: e.target.value }))}
                              />
                            ) : (
                              item.current_text || formatAmount(item.current_amount)
                            )}
                          </td>
                          <td className="is-gap">{isPolicyCoverage ? "-" : formatAmount(item.gap_amount)}</td>
                          <td>
                            {editing ? (
                              <select
                                className="coverage-edit-select"
                                value={editDraft.status}
                                disabled={saving}
                                onChange={(e) => setEditDraft((current) => ({ ...current, status: e.target.value }))}
                              >
                                <option value="충분">충분</option>
                                <option value="부족">부족</option>
                                <option value="미가입">미가입</option>
                              </select>
                            ) : (
                              <>
                                {icon ? <span className="coverage-status-icon" aria-hidden="true">{icon}</span> : null}
                                {isPolicyCoverage ? <span className="coverage-badge coverage-badge--unknown">보유</span> : <CoverageBadge status={item.status} />}
                              </>
                            )}
                          </td>
                          <td>
                            {isPolicyCoverage ? (
                              <span className="coverage-edit-guide">원장</span>
                            ) : editing ? (
                              <div className="coverage-row-actions">
                                <button type="button" className="coverage-button compact" onClick={() => void saveEditingRow(item)} disabled={saving}>
                                  {saving ? "저장 중…" : "저장"}
                                </button>
                                <button type="button" className="coverage-button compact" onClick={cancelEditingRow} disabled={saving}>
                                  취소
                                </button>
                              </div>
                            ) : (
                              <button type="button" className="coverage-button compact" onClick={() => startEditingRow(item)} disabled={!!editingRowId}>
                                수정
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}
    </main>
  );
}
