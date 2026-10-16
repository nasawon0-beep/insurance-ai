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
  matched_policy_ids?: string[];  // 매칭된 policy ID 목록
  matched_policy_coverage_ids?: string[];  // 3단에 표시할 policy_coverages 행 ID 목록
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
  audience: "customer" | "internal";
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

type CoverageTreeNode = {
  id: string;
  level: 1 | 2 | 3;
  name: string;
  status: string | null;
  current_amount: number | null;
  gap_amount: number | null;
  recommended_amount?: number | null;  // 2단에서 사용
  children?: CoverageTreeNode[];
  isExpanded?: boolean;
  metadata?: {
    insurer?: string;
    product_name?: string;
    rider_name?: string;
    coverage_name?: string;  // 3단 원본 담보명
    source_page?: number;
    customer_summary?: string;
    internal_memo?: string;
  };
};

const RECENT_CUSTOMERS_KEY = "coverageAnalysis.recentCustomerIds";
const DEBUG_PREFIX = "[CoverageAnalysis]";

// 1단 카테고리 고정 순서 (메리츠 기준)
const CATEGORY_ORDER = [
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
];

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
  const amount = Number(value || 0);
  const eok = Math.floor(amount / 100000000);
  const man = Math.floor((amount % 100000000) / 10000);
  
  if (eok > 0 && man > 0) {
    return `${eok.toLocaleString("ko-KR")}억${man.toLocaleString("ko-KR")}만원`;
  } else if (eok > 0) {
    return `${eok.toLocaleString("ko-KR")}억원`;
  } else {
    return `${man.toLocaleString("ko-KR")}만원`;
  }
}

function countByStatus(items: CoverageItem[], status: string): number {
  return items.filter((item) => item.status === status).length;
}

// 카테고리 그룹 정규화 (메리츠 기준)
function normalizeCategory(categoryGroup: string): string {
  const mapping: Record<string, string> = {
    "암": "암",
    "뇌혈관질환": "뇌혈관질환",
    "뇌": "뇌혈관질환",
    "심장질환": "심장질환",
    "심장": "심장질환",
    "치매": "치매",
    "실손의료비": "실손의료비",
    "실손": "실손의료비",
    "수술비": "수술비",
    "수술": "수술비",
    "입원비": "입원비(일당)·치료비",
    "일당": "입원비(일당)·치료비",
    "치료비": "입원비(일당)·치료비",
    "사망": "사망·후유장해",
    "후유장해": "사망·후유장해",
    "운전자": "운전자·법률배상·기타",
    "법률": "운전자·법률배상·기타",
    "배상책임": "운전자·법률배상·기타",
    "배상": "운전자·법률배상·기타",
    "치아": "운전자·법률배상·기타",
    "화상": "운전자·법률배상·기타",
    "골절": "운전자·법률배상·기타",
  };
  
  for (const [key, value] of Object.entries(mapping)) {
    if (categoryGroup.includes(key)) {
      return value;
    }
  }
  
  return "기타";
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

function buildCoverageTree(
  categories: CoverageCategory[], 
  policyCoverages: PolicyCoverage[], 
  expandedNodes: Set<string>,
  activeTab: string
): CoverageTreeNode[] {
  const tree: CoverageTreeNode[] = [];
  
  // PolicyCoverage ID → PolicyCoverage 맵 생성
  const policyCoverageMap = new Map<string, PolicyCoverage>();
  for (const policy of policyCoverages) {
    policyCoverageMap.set(policy.id, policy);
  }
  
  // 카테고리별 데이터 수집
  const categoryDataMap = new Map<string, {
    items: CoverageItem[];
  }>();
  
  // 초기화
  for (const cat of CATEGORY_ORDER) {
    categoryDataMap.set(cat, { items: [] });
  }
  
  // 분석 데이터 분류
  for (const category of categories) {
    const normalized = normalizeCategory(category.category_group);
    const data = categoryDataMap.get(normalized);
    if (data) {
      data.items.push(...category.items);
    } else {
      // 기타로 분류
      const otherData = categoryDataMap.get("기타");
      if (otherData) {
        otherData.items.push(...category.items);
      }
    }
  }
  
  // 1단 카테고리 노드 생성 (순서대로)
  for (const categoryName of CATEGORY_ORDER) {
    if (categoryName === "전체") continue;
    
    // 탭 필터링
    if (activeTab !== "전체" && activeTab !== categoryName) continue;
    
    const data = categoryDataMap.get(categoryName);
    if (!data || data.items.length === 0) continue;
    
    const categoryNode: CoverageTreeNode = {
      id: `cat-${categoryName}`,
      level: 1,
      name: categoryName, // 이름만 표시 (개수, 금액 제거)
      status: null,
      current_amount: null,
      gap_amount: null,
      children: [],
      isExpanded: true, // 1단은 항상 펼침
    };
    
    // 2단: 세부 담보명별 그룹핑 (CoverageItem 기준)
    const subcategoryMap = new Map<string, CoverageItem[]>();
    
    for (const item of data.items) {
      const subcategory = item.coverage_name;
      if (!subcategoryMap.has(subcategory)) {
        subcategoryMap.set(subcategory, []);
      }
      subcategoryMap.get(subcategory)!.push(item);
    }
    
    // 2단 노드 생성
    for (const [subcategoryName, items] of subcategoryMap) {
      const firstItem = items[0];
      if (!firstItem) continue;
      
      // 분석 결과의 계약 ID와 policy_coverages 행 ID는 서로 다르므로,
      // 백엔드가 제공한 실제 담보 행 ID로 3단 상품 목록을 구성한다.
      const matchedPolicies: PolicyCoverage[] = [];
      const matchedCoverageIds = Array.isArray(firstItem.matched_policy_coverage_ids)
        ? firstItem.matched_policy_coverage_ids
        : (Array.isArray(firstItem.matched_policy_ids) ? firstItem.matched_policy_ids : []);
      for (const coverageId of matchedCoverageIds) {
          const policy = policyCoverageMap.get(coverageId);
          if (policy) {
            matchedPolicies.push(policy);
          }
      }
      
      // 2단 상태: 가입 / 미가입
      const enrollmentStatus = firstItem.current_amount > 0 ? "가입" : "미가입";
      const subcategoryId = `sub-${categoryName}-${subcategoryName}`;
      
      // 가입 상품은 초기 펼침, 미가입은 접힘
      const isEnrolled = firstItem.current_amount > 0;
      const shouldExpand = isEnrolled || expandedNodes.has(subcategoryId);
      
      const subcategoryNode: CoverageTreeNode = {
        id: subcategoryId,
        level: 2,
        name: subcategoryName,
        status: enrollmentStatus,
        current_amount: firstItem.current_amount,
        gap_amount: null, // 부족금액 제거
        recommended_amount: null, // 권장금액 제거
        children: [],
        isExpanded: shouldExpand,
        metadata: {
          customer_summary: firstItem.customer_summary,
          internal_memo: firstItem.internal_memo,
        },
      };
      
      // 3단: 매칭된 상품 상세 (회사명 + 상품명 + 담보명 + 금액)
      for (const policy of matchedPolicies) {
        const policyNode: CoverageTreeNode = {
          id: `pol-${policy.id}`,
          level: 3,
          name: policy.insurer || "보험사 미확인",
          status: null,
          current_amount: policy.amount || 0,
          gap_amount: null,
          metadata: {
            insurer: policy.insurer || undefined,
            product_name: policy.product_name || undefined,
            rider_name: policy.rider_name || policy.standard_name || undefined,
            coverage_name: policy.rider_name || policy.standard_name || undefined,
            source_page: policy.source_page || undefined,
          },
        };
        subcategoryNode.children!.push(policyNode);
      }
      
      categoryNode.children!.push(subcategoryNode);
    }
    
    // 카테고리 합계 계산
    categoryNode.current_amount = categoryNode.children!.reduce(
      (sum, node) => sum + (node.current_amount || 0), 
      0
    );
    
    tree.push(categoryNode);
  }
  
  return tree;
}

type CoverageTreeRowProps = {
  node: CoverageTreeNode;
  onToggle: (nodeId: string) => void;
};

function CoverageTreeRow({ node, onToggle }: CoverageTreeRowProps) {
  const hasChildren = node.children && node.children.length > 0;
  const indent = (node.level - 1) * 24; // px
  
  // 2단: 테이블 형태 (보장명 | 상태 | 현재보장)
  if (node.level === 2) {
    return (
      <>
        <tr className={`coverage-tree-row coverage-tree-row--level${node.level}`}>
          <td 
            style={{ paddingLeft: `${indent}px` }}
            onClick={hasChildren ? () => onToggle(node.id) : undefined}
            className={hasChildren ? "coverage-tree-node" : ""}
          >
            <div className="coverage-tree-cell">
              {hasChildren && (
                <button 
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggle(node.id);
                  }}
                  className="coverage-tree-toggle"
                  aria-expanded={node.isExpanded}
                >
                  {node.isExpanded ? "▼" : "▶"}
                </button>
              )}
              <span className="coverage-tree-name">{node.name}</span>
            </div>
          </td>
          <td>
            {node.status && <CoverageBadge status={node.status} />}
          </td>
          <td className="coverage-level2-current">
            {node.current_amount !== null ? formatAmount(node.current_amount) : "-"}
          </td>
        </tr>
        {node.isExpanded && node.children?.map(child => (
          <CoverageTreeRow key={child.id} node={child} onToggle={onToggle} />
        ))}
      </>
    );
  }
  
  // 3단: 상품 상세 (4줄: 회사명, 상품명, 담보명, 금액)
  if (node.level === 3) {
    return (
      <tr className={`coverage-tree-row coverage-tree-row--level${node.level}`}>
        <td style={{ paddingLeft: `${indent}px` }} colSpan={2}>
          <div className="coverage-level3-detail">
            <div className="coverage-level3-company">{node.metadata?.insurer || node.name}</div>
            {node.metadata?.product_name && (
              <div className="coverage-level3-product">{node.metadata.product_name}</div>
            )}
            {node.metadata?.coverage_name && (
              <div className="coverage-level3-coverage">{node.metadata.coverage_name}</div>
            )}
            {node.current_amount !== null && (
              <div className="coverage-level3-amount-text">{formatAmount(node.current_amount)}</div>
            )}
          </div>
        </td>
        <td className="coverage-level3-empty"></td>
      </tr>
    );
  }
  
  // 1단: 카테고리 (항상 펼침, 토글 버튼 제거)
  return (
    <>
      <tr className={`coverage-tree-row coverage-tree-row--level${node.level}`}>
        <td 
          style={{ paddingLeft: `${indent}px` }}
          className=""
        >
          <div className="coverage-tree-cell">
            <span className="coverage-tree-name">{node.name}</span>
          </div>
        </td>
        <td colSpan={2}></td>
      </tr>
      {node.children?.map(child => (
        <CoverageTreeRow key={child.id} node={child} onToggle={onToggle} />
      ))}
    </>
  );
}

export default function CoverageAnalysisScreen() {
  const [customers, setCustomers] = useState<CustomerOption[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerSearchOpen, setCustomerSearchOpen] = useState(false);
  const [recentCustomerIds, setRecentCustomerIds] = useState<string[]>([]);
  const [data, setData] = useState<CoverageResponse | null>(null);
  const [policyCoverages, setPolicyCoverages] = useState<PolicyCoverage[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState(
    (import.meta as any).env?.VITE_INITIAL_COVERAGE_TAB || "전체",
  );
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
      setCustomerId((current) => {
        if (current && list.some((customer) => customer.id === current)) return current;
        const initialCustomerId = (import.meta as any).env?.VITE_INITIAL_COVERAGE_CUSTOMER_ID;
        const initialCustomer = list.find((customer) => customer.id === initialCustomerId);
        if (initialCustomer) {
          setCustomerQuery(initialCustomer.name);
          return initialCustomer.id;
        }
        return "";
      });
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
    } catch (e) {
      setData(null);
      setPolicyCoverages([]);
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
    if (customerId) {
      void loadAnalysis();
    }
  }, [customerId, loadAnalysis]);

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
  
  const coverageTree = useMemo(
    () => data ? buildCoverageTree(data.categories, policyCoverages, expandedNodes, activeTab) : [],
    [data, policyCoverages, expandedNodes, activeTab],
  );
  
  const toggleTreeNode = useCallback((nodeId: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }, []);

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

  return (
    <main className="coverage-page">
      <header className="coverage-header">
        <div>
          <h2 className="coverage-title">보장분석</h2>
          <p className="coverage-subtitle">3단 계층으로 보장분석서를 표시합니다.</p>
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
            <p className="coverage-summary-label">표시 항목</p>
            <p className="coverage-summary-value">
              {analysisItems.length}개
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
          <div className="coverage-tabs" role="tablist" aria-label="보장 카테고리 탭">
            {CATEGORY_ORDER.map((category) => (
              <button
                key={category}
                type="button"
                role="tab"
                className={`coverage-tab ${activeTab === category ? "is-active" : ""}`}
                aria-selected={activeTab === category}
                onClick={() => setActiveTab(category)}
              >
                {category}
              </button>
            ))}
          </div>

          <div className="coverage-table-card" role="tabpanel">
            <div className="coverage-table-heading">
              <h3>보장분석</h3>
              <span>{coverageTree.length}개 카테고리</span>
            </div>

            {coverageTree.length === 0 ? (
              <div className="coverage-empty in-panel">표시할 세부 담보가 없습니다.</div>
            ) : (
              <div className="coverage-table-wrap">
                <table className="coverage-table coverage-tree-table">
                  <thead>
                    <tr>
                      <th>보장명</th>
                      <th>상태</th>
                      <th>현재보장</th>
                    </tr>
                  </thead>
                  <tbody>
                    {coverageTree.map((node) => (
                      <CoverageTreeRow key={node.id} node={node} onToggle={toggleTreeNode} />
                    ))}
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
