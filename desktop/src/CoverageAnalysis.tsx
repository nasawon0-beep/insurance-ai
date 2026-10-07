import { useCallback, useEffect, useMemo, useState } from "react";
import CoverageBadge from "./components/CoverageBadge";
import { COVERAGE_CATALOG, findCoverageCatalogItem, normalizeCoverageCatalogName } from "./coverageCatalog";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";
import { parseCoverageJson, type CoverageRow } from "./coverageRows";
import "./styles/coverage.css";

type Audience = "customer" | "internal";

type CustomerOption = {
  id: string;
  name: string;
  phone?: string | null;
  birth_date?: string | null;
};

type Consultation = {
  id: string;
  title?: string | null;
  consulted_at?: string | null;
  coverage_json: string | null;
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
  audience: Audience;
  summary?: {
    overall?: string;
    total_items?: number;
    displayed_items?: number;
    counts?: Record<string, number>;
    priority_counts?: Record<string, number>;
  };
  categories: CoverageCategory[];
};

type CoverageTab = {
  key: string;
  label: string;
  aliases?: string[];
};

type EditDraft = {
  status: string;
  current: string;
  recommended: string;
};

type CoverageFilter = "전체" | "부족" | "미가입" | "충분" | "암" | "뇌혈관" | "심장" | "수술비" | "입원비" | "운전자" | "기타";

const COVERAGE_TABS: CoverageTab[] = [
  { key: "death", label: "사망" },
  { key: "disability", label: "후유장해" },
  { key: "cancer", label: "암" },
  { key: "brain", label: "뇌혈관질환" },
  { key: "heart", label: "심장질환" },
  { key: "dementia", label: "치매" },
  { key: "medical", label: "실손의료비" },
  { key: "surgery", label: "수술비" },
  { key: "hospital", label: "입원비/일당", aliases: ["입원비 / 일당"] },
  { key: "treatment", label: "치료비" },
  { key: "driver", label: "운전자" },
  { key: "liability", label: "법률/배상책임", aliases: ["법률 / 배상책임"] },
  { key: "dental", label: "치아/화상/골절", aliases: ["치아 / 화상 / 골절"] },
];

const RECENT_CUSTOMERS_KEY = "coverageAnalysis.recentCustomerIds";
const DEBUG_PREFIX = "[CoverageAnalysis]";
const COVERAGE_FILTERS: CoverageFilter[] = ["전체", "부족", "미가입", "충분", "암", "뇌혈관", "심장", "수술비", "입원비", "운전자", "기타"];

function categoryIdForCoverageName(name: string): string | null {
  const catalogItem = findCoverageCatalogItem(name);
  if (!catalogItem) return null;
  const tab = COVERAGE_TABS.find((item) => [item.label, ...(item.aliases ?? [])].includes(catalogItem.category));
  return tab?.key ?? null;
}

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
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  let res: Response;
  try {
    console.log(`${DEBUG_PREFIX} api:request`, { method: init?.method ?? "GET", path });
    res = await engineFetch(path, { ...init, headers });
    console.log(`${DEBUG_PREFIX} api:response`, { method: init?.method ?? "GET", path, status: res.status, ok: res.ok });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    throw new Error(`로컬 엔진 연결 실패 (${path}): ${message}. local-engine 실행 상태, CORS origin, 네트워크를 확인하세요.`);
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

function categoryStatusSummary(items: CoverageItem[]): string {
  if (items.some((item) => item.status === "미가입")) return "미가입";
  if (items.some((item) => item.status === "부족")) return "부족";
  if (items.length > 0 && items.every((item) => item.status === "충분")) return "충분";
  return "확인필요";
}

function buildCoverageResponseFromRows(customerId: string, rows: CoverageRow[], audience: Audience): CoverageResponse {
  const categories: CoverageCategory[] = COVERAGE_TABS.map((tab) => ({
    category_id: tab.key,
    category_group: tab.label,
    status_summary: "확인필요",
    max_priority: "",
    items: [],
  }));
  const categoryById = new Map(categories.map((category) => [category.category_id, category]));
  const rowsByName = new Map(rows.map((row, index) => [normalizeCoverageCatalogName(row.name), { row, index }]));

  for (const catalogItem of COVERAGE_CATALOG) {
    const matched = rowsByName.get(normalizeCoverageCatalogName(catalogItem.name));
    const matchedRow = matched?.row;
    const categoryId = categoryIdForCoverageName(catalogItem.name);
    if (!categoryId) continue;
    const category = categoryById.get(categoryId);
    if (!category) continue;
    const currentAmount = matchedRow?.current_amount ?? parseWonAmount(matchedRow?.current);
    const recommendedAmount = matchedRow?.recommended_amount ?? (matchedRow?.recommended ? parseWonAmount(matchedRow.recommended) : catalogItem.recommended);
    const gapAmount = matchedRow?.shortage_amount ?? Math.max(0, recommendedAmount - currentAmount);
    category.items.push({
      id: catalogItem.id,
      source_index: matched?.index ?? -1,
      category_id: categoryId,
      category_group: category.category_group,
      coverage_name: catalogItem.name,
      status: matchedRow?.status || "미가입",
      current_text: matchedRow?.current ?? "0만원",
      recommended_text: matchedRow?.recommended ?? formatAmount(catalogItem.recommended),
      recommended_amount: recommendedAmount,
      current_amount: currentAmount,
      gap_amount: gapAmount,
      customer_summary: matchedRow ? `보장별 상세표 기준 · 충족률 ${matchedRow.pct}%` : "coverage.py 카탈로그 기준 · PDF 미검출 미가입",
      internal_memo: matchedRow ? `consultations.coverage_json 원본: 현재 ${matchedRow.current ?? "-"} / 권장 ${matchedRow.recommended ?? "-"}` : "consultations.coverage_json에 없는 항목을 미가입으로 보강",
      evidence_pages: matchedRow?.source_page ? [matchedRow.source_page] : undefined,
    });
  }

  for (const category of categories) category.status_summary = categoryStatusSummary(category.items);
  const allItems = categories.flatMap((category) => category.items);
  return {
    customer_id: customerId,
    run_id: "consultations.coverage_json",
    status: allItems.length ? "completed" : "empty",
    audience,
    summary: {
      overall: "consultations.coverage_json 기준 보장분석입니다.",
      total_items: COVERAGE_CATALOG.length,
      displayed_items: allItems.length,
      counts: {
        충분: countByStatus(allItems, "충분"),
        부족: countByStatus(allItems, "부족"),
        미가입: countByStatus(allItems, "미가입"),
      },
    },
    categories,
  };
}

// Static guard for coverage_json category mapping tests.
categoryIdForCoverageName("질병사망") === "death";
categoryIdForCoverageName("유사암 진단비") === "cancer";
categoryIdForCoverageName("뇌혈관 진단비") === "brain";
categoryIdForCoverageName("급성심근경색 진단비") === "heart";

export default function CoverageAnalysisScreen() {
  const [customers, setCustomers] = useState<CustomerOption[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerSearchOpen, setCustomerSearchOpen] = useState(false);
  const [recentCustomerIds, setRecentCustomerIds] = useState<string[]>([]);
  const [audience, setAudience] = useState<Audience>("customer");
  const [data, setData] = useState<CoverageResponse | null>(null);
  const [latestConsultation, setLatestConsultation] = useState<Consultation | null>(null);
  const [coverageRows, setCoverageRows] = useState<CoverageRow[]>([]);
  const [editingRowId, setEditingRowId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({ status: "미가입", current: "", recommended: "" });
  const [savingRowId, setSavingRowId] = useState<string | null>(null);
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("전체");
  const [customersBusy, setCustomersBusy] = useState(false);
  const [analysisBusy, setAnalysisBusy] = useState(false);
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

  const loadCustomers = useCallback(async () => {
    setCustomersBusy(true);
    setError(null);
    try {
      const body = await api<{ customers?: CustomerOption[] } | null>("/customers");
      const list = Array.isArray(body?.customers) ? body.customers : [];
      setCustomers(list);
      setCustomerId((current) => (current && list.some((customer) => customer.id === current) ? current : ""));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      setLatestConsultation(null);
      setCoverageRows([]);
      setEditingRowId(null);
      return;
    }
    setAnalysisBusy(true);
    setError(null);
    try {
      const body = await api<{ consultations?: Consultation[] } | Consultation[] | null>(`/customers/${customerId}/consultations`);
      const consultations = Array.isArray(body) ? body : Array.isArray(body?.consultations) ? body.consultations : [];
      const latest = consultations.find((consultation) => typeof consultation?.coverage_json === "string" && consultation.coverage_json.trim());
      if (!latest?.coverage_json) {
        setData(null);
        setLatestConsultation(null);
        setCoverageRows([]);
        setEditingRowId(null);
        return;
      }
      const parsed = parseCoverageJson(latest.coverage_json);
      if (parsed.corrupt) {
        setData(null);
        setLatestConsultation(null);
        setCoverageRows([]);
        setEditingRowId(null);
        setError("저장된 보장분석 데이터 형식이 손상되었습니다. 고객 목록에서 다시 분석하거나 표를 새로 만들어 주세요.");
        return;
      }
      setLatestConsultation(latest);
      setCoverageRows(parsed.rows);
      setEditingRowId(null);
      setData(buildCoverageResponseFromRows(customerId, parsed.rows, audience));
    } catch (e) {
      setData(null);
      setLatestConsultation(null);
      setCoverageRows([]);
      setEditingRowId(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalysisBusy(false);
    }
  }, [audience, customerId]);

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
    if (!latestConsultation) {
      setError("저장할 상담 이력을 찾을 수 없습니다. 새로고침 후 다시 시도하세요.");
      return;
    }
    const sourceRow = coverageRows[item.source_index];
    if (item.source_index >= 0 && !sourceRow) {
      setError("저장할 보장 항목을 찾을 수 없습니다. 새로고침 후 다시 시도하세요.");
      return;
    }
    setSavingRowId(item.id);
    setError(null);
    try {
      const editedRow: CoverageRow = {
        ...(sourceRow ?? { name: item.coverage_name, pct: 0 }),
        status: editDraft.status,
        current: editDraft.current.trim() || null,
        recommended: editDraft.recommended.trim() || null,
      };
      const nextRows = item.source_index >= 0
        ? coverageRows.map((row, index) => (index === item.source_index ? editedRow : row))
        : [...coverageRows, editedRow];
      await api(`/consultations/${latestConsultation.id}`, {
        method: "PATCH",
        body: JSON.stringify({ coverage_json: JSON.stringify(nextRows) }),
      });
      setEditingRowId(null);
      setCoverageRows(nextRows);
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
          <p className="coverage-subtitle">고객을 검색하고 필요한 보장 카테고리를 탭으로 확인합니다.</p>
        </div>
      </header>

      <section className="coverage-toolbar" aria-label="보장분석 조건">
        <div className="coverage-field">
          <label htmlFor="coverage-customer">고객 선택</label>
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
          <div>
            <div className="coverage-toggle-label">표시 대상</div>
            <div className="coverage-toggle" role="group" aria-label="고객용 내부용 전환">
              <button
                type="button"
                className={audience === "customer" ? "is-active" : ""}
                onClick={() => setAudience("customer")}
              >
                고객용
              </button>
              <button
                type="button"
                className={audience === "internal" ? "is-active" : ""}
                onClick={() => setAudience("internal")}
              >
                내부용
              </button>
            </div>
          </div>
          <button type="button" className="coverage-button" onClick={() => void loadAnalysis()} disabled={!customerId || analysisBusy}>
            {analysisBusy ? "불러오는 중…" : "새로고침"}
          </button>
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
            <p className="coverage-summary-label">전체/미가입/부족/충분</p>
            <p className="coverage-summary-value">
              {allItems.length} / {countByStatus(allItems, "미가입")} / {countByStatus(allItems, "부족")} / {countByStatus(allItems, "충분")}
            </p>
          </div>
        </section>
      )}

      {error && <div className="coverage-error">오류: {error}</div>}
      {!error && !data && customerId && (
        <div className="coverage-empty">
          {analysisBusy ? "보장분석 결과를 불러오는 중…" : "저장된 보장분석 데이터가 없습니다. 고객 목록에서 수정하세요."}
        </div>
      )}

      {data && (
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
              <h3>보장별 상세표 전체</h3>
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
                      return (
                        <tr key={item.id}>
                          <td>{item.category_group}</td>
                          <td>
                            <div className="coverage-table-title">{item.coverage_name}</div>
                            {item.customer_summary && <p>{item.customer_summary}</p>}
                            {audience === "internal" && (
                              <div className="coverage-internal compact">
                                {item.internal_memo && <div className="coverage-internal-row">내부 메모: {item.internal_memo}</div>}
                                <div className="coverage-internal-row">
                                  우선순위: {item.priority ?? "-"} · 점수: {item.priority_score ?? "-"}
                                  {item.evidence_pages?.length ? ` · 근거 p.${item.evidence_pages.join(", ")}` : ""}
                                </div>
                              </div>
                            )}
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
