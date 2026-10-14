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

type PolicyCoverageItem = {
  id: string;
  insurer?: string | null;
  product_name?: string | null;
  rider_no?: string | null;
  rider_name?: string | null;
  standard_name?: string | null;
  amount?: number | null;
  amount_text?: string | null;
  source_page?: number | null;
  confidence?: number | null;
};

type PolicyCoveragesResponse = {
  policy_coverages: PolicyCoverageItem[];
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
  if (status === "부족") return "!";
  if (status === "미가입") return "—";
  return "?";
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

function inferCoverageGroup(row: PolicyCoverageItem): string {
  const text = `${row.standard_name ?? ""} ${row.rider_name ?? ""}`;
  if (text.includes("암")) return "암";
  if (text.includes("뇌")) return "뇌혈관질환";
  if (text.includes("심장") || text.includes("허혈") || text.includes("심근")) return "심장질환";
  if (text.includes("수술")) return "수술비";
  if (text.includes("입원") || text.includes("일당")) return "입원비 / 일당";
  if (text.includes("운전자") || text.includes("교통") || text.includes("변호사") || text.includes("벌금")) return "운전자";
  if (text.includes("사망")) return "사망";
  if (text.includes("후유장해") || text.includes("장해")) return "후유장해";
  if (text.includes("실손") || text.includes("통원")) return "실손의료비";
  return "기타";
}

function policyCoverageMatches(row: PolicyCoverageItem, filter: CoverageFilter): boolean {
  if (filter === "전체") return true;
  if (["부족", "미가입", "충분"].includes(filter)) return filter === "충분";
  return filterMatches(
    {
      id: row.id,
      source_index: 0,
      category_group: inferCoverageGroup(row),
      coverage_name: row.standard_name ?? row.rider_name ?? "",
      status: "충분",
      current_text: row.amount_text ?? null,
      recommended_text: null,
      recommended_amount: 0,
      current_amount: Number(row.amount ?? 0),
      gap_amount: 0,
    },
    filter,
  );
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
  const [policyCoverageRows, setPolicyCoverageRows] = useState<PolicyCoverageItem[]>([]);
  const [editingRowId, setEditingRowId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({ status: "미가입", current: "", recommended: "" });
  const [savingRowId, setSavingRowId] = useState<string | null>(null);
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("전체");
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
      setPolicyCoverageRows([]);
      setEditingRowId(null);
      return;
    }
    setAnalysisBusy(true);
    setError(null);
    setRefreshMessage(null);
    try {
      const [analysis, policyCoverages] = await Promise.all([
        api<CoverageResponse>(`/customers/${customerId}/coverage-analysis?audience=customer`).catch(() => null),
        api<PolicyCoveragesResponse>(`/customers/${customerId}/policy-coverages`),
      ]);
      setData(analysis);
      setPolicyCoverageRows(Array.isArray(policyCoverages.policy_coverages) ? policyCoverages.policy_coverages : []);
      setEditingRowId(null);
    } catch (e) {
      setData(null);
      setPolicyCoverageRows([]);
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

  const allItems = useMemo(() => (data?.categories ?? []).flatMap((category) => category.items), [data]);
  const showingPolicyCoverages = policyCoverageRows.length > 0;
  const filteredPolicyCoverageRows = useMemo(
    () => policyCoverageRows.filter((row) => policyCoverageMatches(row, coverageFilter)),
    [policyCoverageRows, coverageFilter],
  );
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

  const shortageTotal = countByStatus(allItems, "부족") + countByStatus(allItems, "미가입");

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
          <span className="coverage-edit-guide">
            {showingPolicyCoverages ? "고객 가입목록에 저장된 policy_coverages 전체 담보를 표시합니다." : "각 행의 수정 버튼으로 현재 보장, 권장 금액, 상태를 직접 수정할 수 있습니다."}
          </span>
        </div>
      </section>

      {(data || showingPolicyCoverages) && (
        <section className="coverage-summary" aria-label="전체현황">
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">선택 고객</p>
            <p className="coverage-summary-value">{selectedCustomer?.name ?? "-"}</p>
          </div>
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">부족/미가입</p>
            <p className="coverage-summary-value">{showingPolicyCoverages ? "-" : `${shortageTotal}개`}</p>
          </div>
          <div className="coverage-summary-card">
            <p className="coverage-summary-label">전체 담보</p>
            <p className="coverage-summary-value">
              {showingPolicyCoverages
                ? `${policyCoverageRows.length}개`
                : `${allItems.length} / 미가입 ${countByStatus(allItems, "미가입")} / 부족 ${countByStatus(allItems, "부족")} / 충분 ${countByStatus(allItems, "충분")}`}
            </p>
          </div>
        </section>
      )}

      {error && <div className="coverage-error">오류: {error}</div>}
      {!error && refreshMessage && <div className="coverage-empty">{refreshMessage}</div>}
      {!error && !data && !showingPolicyCoverages && customerId && (
        <div className="coverage-empty">
          {analysisBusy ? "보장분석 결과를 불러오는 중…" : "저장된 보장분석 데이터가 없습니다. 고객 목록에서 수정하세요."}
        </div>
      )}

      {(data || showingPolicyCoverages) && (
        <section className="coverage-analysis-panel" aria-label="보장분석 상세표">
          <div className="coverage-filter-row" role="group" aria-label="보장분석 필터">
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
              <h3>{showingPolicyCoverages ? "보유 담보 전체" : "보장별 상세표 전체"}</h3>
              <span>{coverageFilter} {showingPolicyCoverages ? filteredPolicyCoverageRows.length : filteredItems.length}개</span>
            </div>

            {showingPolicyCoverages ? (
              filteredPolicyCoverageRows.length === 0 ? (
                <div className="coverage-empty in-panel">표시할 담보가 없습니다.</div>
              ) : (
                <div className="coverage-table-wrap">
                  <table className="coverage-table">
                    <thead>
                      <tr>
                        <th>보장군</th>
                        <th>보험사</th>
                        <th>상품명</th>
                        <th>담보명</th>
                        <th>표준명</th>
                        <th>가입금액</th>
                        <th>페이지</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredPolicyCoverageRows.map((row) => (
                        <tr key={row.id}>
                          <td>{inferCoverageGroup(row)}</td>
                          <td>{row.insurer || "-"}</td>
                          <td>{row.product_name || "-"}</td>
                          <td><div className="coverage-table-title">{row.rider_name || "-"}</div></td>
                          <td>{row.standard_name || "-"}</td>
                          <td>{row.amount_text || formatAmount(row.amount)}</td>
                          <td>{row.source_page ? `p.${row.source_page}` : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            ) : filteredItems.length === 0 ? (
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
                      return (
                        <tr key={item.id}>
                          <td>{item.category_group}</td>
                          <td>
                            <div className="coverage-table-title">{item.coverage_name}</div>
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
                          <td className="is-gap">{formatAmount(item.gap_amount)}</td>
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
                                <span className="coverage-status-icon" aria-hidden="true">{statusIcon(item.status)}</span>
                                <CoverageBadge status={item.status} />
                              </>
                            )}
                          </td>
                          <td>
                            {editing ? (
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
