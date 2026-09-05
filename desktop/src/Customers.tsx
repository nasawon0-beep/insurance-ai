import { useCallback, useEffect, useRef, useState } from "react";
import {
  CoverageTableEditable,
  CoverageTableEditableSafe,
  type CoverageRow,
  PolicyList,
  savePolicies,
  STATUS_LABEL,
  type PolicyDraft,
} from "./PolicyList";
import { rrnEnabled } from "./auth";
import { track } from "./usage";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";
import { totalMonthlyPremium, expiringCount } from "./customerKpi";
import { policyProgressPct } from "./policyProgress";
import { computeMergeRows, memoAppend, type MergeRow } from "./mergeDiff";
import { normalizeDate } from "./normalizeDate";
import { hasPendingDraft } from "./analysisDraft";

type Customer = {
  id: string;
  name: string;
  phone: string | null;
  birth_date: string | null;
  gender: string | null;
  email: string | null;
  address: string | null;
  occupation: string | null;
  tags: string[];
  memo: string | null;
  has_rrn: boolean;
  rrn: string | null;
  rrn_masked: string | null;
  policy_count: number;
  active_policy_count: number;
  soonest_expiry: string | null;
  next_follow_up: string | null;
  last_consulted_at: string | null;
  customer_status: string | null;
  effective_status: string;
  next_birthday: string | null;
  birth_date_estimated: boolean;
  first_registered_ym: string | null;
  own_policy_count?: number;
  created_at: string;
  updated_at: string;
};

type Wrap = <Args extends unknown[]>(
  fn: (...args: Args) => Promise<unknown>,
  rethrow?: boolean,
) => (...args: Args) => Promise<void>;

type Policy = {
  id: string;
  customer_id: string;
  insurer: string | null;
  product_name: string | null;
  policy_number: string | null;
  plan_type: string | null;
  premium: number | null;
  payment_cycle: string | null;
  start_date: string | null;
  end_date: string | null;
  status: string;
  memo: string | null;
  document_id: string | null;
  insured_period: string | null;
  payment_period: string | null;
  payment_end_date: string | null;
  end_date_derived: number | null;
  is_own?: boolean | null;
  // 계약자(피보험자와 다를 때). customer_id 는 피보험자. 비어있으면 본인계약.
  policyholder_name?: string | null;
  policyholder_rel?: string | null;
};

type Consultation = {
  id: string;
  customer_id: string;
  consulted_at: string;
  channel: string | null;
  title: string | null;
  content: string | null;
  transcript: string | null;
  follow_up_at: string | null;
  follow_up_done_at: string | null;
  coverage_json: string | null;
};

type CustomerDetail = Customer & { policies: Policy[]; consultations: Consultation[] };

type AskResult = {
  answer: string;
  basis?: "clause" | "policy_summary" | "none";
  fallback?: boolean;
  disclaimer?: string | null;
  product?: string | null;
  clause: string | null;
  company: string | null;
  page: number | null;
  pages: number[];
  grounded: boolean;
  abstained: boolean;
  sources: { page: number; filename: string; score: number; text: string }[];
  document_ids: string[];
};

const EMPTY_FORM = {
  name: "",
  phone: "",
  birth_date: "",
  gender: "",
  email: "",
  address: "",
  occupation: "",
  tags: "", // 콤마로 구분해 입력
  customer_status: "", // "" = 미지정(신규는 서버가 '가망'), 그 외 가망/미가입/해지
  first_registered_ym: "", // 최초 고객등록 년월 (예: 2024-03). 비우면 서버가 현재 년월로 채움.
  memo: "",
};
type Form = typeof EMPTY_FORM;
type DetailTab = "basic" | "policies" | "consultations" | "coverage" | "ask" | "audit";

type AuditItem = {
  id: string; actor: string; action: string; entity: string;
  fields: string | null; created_at: string;
};

// 고객 상태 칩 색 (effective_status: 가입/가망/미가입/해지)
const STATUS_CHIP: Record<string, string> = {
  가입: "#161",
  가망: "#2563eb",
  미가입: "#888",
  해지: "#b00",
};

function ddayLabel(dateStr: string | null): { label: string; color: string } | null {
  if (!dateStr) return null;
  const d = new Date(dateStr.slice(0, 10) + "T00:00:00");
  if (isNaN(d.getTime())) return null;
  const days = Math.ceil((d.getTime() - Date.now()) / 86400000);
  if (days < 0) return { label: `${-days}일↑`, color: "#b00" };
  if (days <= 7) return { label: `D-${days}`, color: "#c60" };
  if (days <= 30) return { label: `D-${days}`, color: "#888" };
  return null;
}

const EMPTY_POLICY = {
  insurer: "",
  product_name: "",
  policy_number: "",
  plan_type: "",
  premium: "",
  payment_cycle: "MONTHLY",
  start_date: "",
  end_date: "",
  insured_period: "",
  payment_period: "",
  status: "ACTIVE",
  memo: "",
  is_own: true, // "내 계약" — 기본 ON (상담자가 입력하는 계약 대부분이 본인 판매분)
  policyholder_name: "", // 계약자 (피보험자와 다를 때만). 비우면 본인계약.
  policyholder_rel: "", // 계약자와 피보험자의 관계
};
type PolicyForm = typeof EMPTY_POLICY;

// 계약자(피보험자)와의 관계 드롭다운 옵션. "" = 본인계약.
const POLICYHOLDER_RELS = [
  "",
  "본인",
  "배우자",
  "부",
  "모",
  "자녀",
  "형제자매",
  "사업자",
  "기타",
];

const EMPTY_CONSULT = {
  consulted_at: "",
  channel: "방문",
  title: "",
  content: "",
  follow_up_at: "",
};
type ConsultForm = typeof EMPTY_CONSULT;

async function api(path: string, init?: RequestInit) {
  const res = await engineFetch(path, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(formatErrorDetail(body?.detail) ?? `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

const inputStyle: React.CSSProperties = { padding: 6, width: "100%", boxSizing: "border-box" };
const badge = (bg: string): React.CSSProperties => ({
  background: bg,
  color: "#fff",
  borderRadius: 4,
  padding: "1px 6px",
  fontSize: 11,
  marginLeft: 6,
});

/** end_date(YYYY-MM-DD) 기준 만기 배지: 지남 / 30일 이내 / 그 외 null */
function expiryBadge(endDate: string | null) {
  if (!endDate) return null;
  const end = new Date(endDate + "T00:00:00");
  if (isNaN(end.getTime())) return null;
  const days = Math.ceil((end.getTime() - Date.now()) / 86400000);
  if (days < 0) return <span style={badge("#b00")}>만기 지남</span>;
  if (days <= 30) return <span style={badge("#c60")}>만기 임박 {days}일</span>;
  return null;
}

function statusChip(status: string | null | undefined) {
  if (!status) return null;
  return <span style={{ ...badge(STATUS_CHIP[status] ?? "#888"), marginLeft: 0 }}>{status}</span>;
}

/** 남은 일수 배지 (management 목록의 days / days_until 용) */
function daysBadge(days: number | null | undefined) {
  if (days == null) return null;
  const color = days < 0 ? "#b00" : days <= 7 ? "#c60" : "#888";
  const label = days < 0 ? `${-days}일 지남` : days === 0 ? "오늘" : `D-${days}`;
  return <span style={{ ...badge(color), marginLeft: 0 }}>{label}</span>;
}

function channelIcon(ch: string | null): string {
  const c = ch || "";
  if (c.includes("전화") || c.includes("녹취")) return "📞";
  if (c.includes("방문")) return "🤝";
  if (c.includes("보장분석")) return "📊";
  if (c.includes("제안") || c.includes("증권") || c.includes("보험문서") || c.includes("청약")) return "📄";
  return "💬";
}

function chosungOf(name: string): string {
  const c = (name || "").trim().charCodeAt(0);
  if (Number.isNaN(c)) return "기타";
  if (c >= 0xac00 && c <= 0xd7a3) {
    const CHO = ["ㄱ", "ㄱ", "ㄴ", "ㄷ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅂ", "ㅅ", "ㅅ", "ㅇ", "ㅈ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"];
    return CHO[Math.floor((c - 0xac00) / 588)];
  }
  if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) return String.fromCharCode(c).toUpperCase();
  if (c >= 48 && c <= 57) return "0-9";
  return "기타";
}
const GROUP_ORDER = [
  "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅅ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
  ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ".split(""),
  "0-9", "기타",
];

export default function CustomersScreen({
  focusCustomer = null,
  focusListView = null,
  screen = "list",
  onOpenCustomer,
  onFocusConsumed,
  onListViewConsumed,
  onDraftChange,
}: {
  focusCustomer?: { id: string; nonce: number } | null;
  focusListView?: { view: "expiry" | "birthday"; nonce: number } | null;
  screen?: "new" | "list";
  onOpenCustomer?: (id: string) => void;
  onFocusConsumed?: () => void;
  onListViewConsumed?: () => void;
  // 상위(App)가 상단 탭 전환 전에 미저장 분석 초안·분석 진행 여부를 물어볼 수 있게 알린다.
  onDraftChange?: (state: { hasDraft: boolean; isAnalyzing: boolean }) => void;
}) {
  const [list, setList] = useState<Customer[]>([]);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const [detail, setDetail] = useState<CustomerDetail | null>(null);
  const detailGenRef = useRef(0);
  const listGenRef = useRef(0);
  const listAbortRef = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<"none" | "new" | "edit">(screen === "new" ? "new" : "none");
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [rrnInput, setRrnInput] = useState(""); // 편집할 주민번호 (편집 모드면 전체값이 채워짐)
  const [intakeText, setIntakeText] = useState("");
  const [intakeContext, setIntakeContext] = useState(""); // 업로드 파일에 대한 설명·요청 (분석에만 반영, 저장 안 함)
  const [stagedIntake, setStagedIntake] = useState<File | null>(null); // 끌어다 놓은/고른 파일 — 설명 받고 분석
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkRows, setBulkRows] = useState<any[]>([]);
  const [intakeBusy, setIntakeBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null); // 등록을 막지 않는 안내
  const [sort, setSort] = useState("name");
  const [filterExpiring, setFilterExpiring] = useState(false);
  const [filterFollowUp, setFilterFollowUp] = useState(false);
  const [ownOnly, setOwnOnly] = useState(() => localStorage.getItem("iai.own_only") === "1");
  const [filterTag, setFilterTag] = useState("");
  const [statusFilter, setStatusFilter] = useState(""); // "" | 가입 | 미가입 | 가망 | 해지
  const [listMode, setListMode] = useState<"normal" | "expiry" | "birthday">("normal");
  const [mgmt, setMgmt] = useState<{ expiring_policies: any[]; birthdays: any[] } | null>(null);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [pendingConsult, setPendingConsult] = useState<Record<string, unknown> | null>(null);
  const [pendingPolicies, setPendingPolicies] = useState<PolicyDraft[]>([]);
  const [policyChecked, setPolicyChecked] = useState<boolean[]>([]);
  const [pendingDocType, setPendingDocType] = useState<string | undefined>(undefined);
  const [pendingCoverage, setPendingCoverage] = useState<CoverageRow[]>([]);
  const [audioIntakeBusy, setAudioIntakeBusy] = useState(false);
  const [fileKind, setFileKind] = useState<"audio" | "doc" | null>(null); // 분석 중 파일 종류
  const [dragOver, setDragOver] = useState(false);
  const [dupMatch, setDupMatch] = useState<{ id: string; name: string; reason: string } | null>(null);
  const [mergePreview, setMergePreview] = useState<{
    existing: Record<string, unknown>;
    rows: MergeRow[];
    memoLine: string | null;
    memoPatch: string | null;
    policyCount: number;
    hasConsult: boolean;
    hasRrn: boolean;
  } | null>(null);
  const [mergeChecked, setMergeChecked] = useState<Record<string, boolean>>({});
  const [pendingDelete, setPendingDelete] = useState(false);
  const [pendingPolicyDelete, setPendingPolicyDelete] = useState<string | null>(null);
  const [pendingConsultDelete, setPendingConsultDelete] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>("basic");
  const [birthDateHint, setBirthDateHint] = useState(false);

  // bulkRows(일괄 인식 목록)도 미저장 분석 결과다 — 저장 없이 떠나면 그대로 유실된다.
  const hasPendingDraftNow = () =>
    hasPendingDraft(pendingPolicies, pendingCoverage, pendingConsult) || bulkRows.length > 0;

  const canLeaveAnalysisDraft = () =>
    !hasPendingDraftNow() || window.confirm("저장하지 않은 분석 결과가 있습니다. 버리고 이동할까요?");

  const canOpenCustomer = (targetId: string) => {
    if (audioIntakeBusy || intakeBusy) {
      window.alert("분석이 진행 중입니다. 완료된 뒤 이동해 주세요.");
      return false;
    }
    if (targetId === selectedId) return false;
    return canLeaveAnalysisDraft();
  };

  // 상단 탭 전환(새 고객 ↔ 고객 목록 ↔ AI 문의 등)은 이 컴포넌트를 통째로 unmount 시켜
  // 내부 가드(canOpenCustomer 등)를 거치지 않는다 — 상위(App)가 대신 물어볼 수 있게 상태를 올려보낸다.
  // hasDraft(미저장 결과 있음 — 확인 후 이동 가능)와 isAnalyzing(분석 진행 중 — 이동 자체를 막음)을
  // 구분해서 올려야, 내부 가드(canOpenCustomer)와 상단 탭 가드의 정책이 일치한다.
  useEffect(() => {
    onDraftChange?.({ hasDraft: hasPendingDraftNow(), isAnalyzing: audioIntakeBusy || intakeBusy });
  }, [pendingPolicies, pendingCoverage, pendingConsult, bulkRows, audioIntakeBusy, intakeBusy, onDraftChange]);

  const openMergePreview = async () => {
    if (!dupMatch) return;
    setError(null);
    setNotice(null);
    try {
      const existing = await api(`/customers/${dupMatch.id}`);
      const rows = computeMergeRows(form as Record<string, string>, existing);
      const memo = memoAppend(form.memo, existing.memo, new Date().toISOString().slice(0, 10));
      const rc = rrnCheck(rrnInput);
      setMergeChecked(Object.fromEntries(rows.map((r) => [r.key, true])));
      setMergePreview({
        existing,
        rows,
        memoLine: memo ? form.memo.trim() : null,
        memoPatch: memo,
        // savePolicies 와 같은 판정: 체크 해제 안 됐고 보험사·상품명·보험료 중 하나라도 있는 계약.
        policyCount: pendingPolicies.filter(
          (p, i) => policyChecked[i] !== false && (p.insurer || p.product_name || p.premium_won),
        ).length,
        hasConsult: pendingConsult != null,
        hasRrn: rrnEnabled() && rc.digits.length > 0 && rc.ok,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const confirmMerge = async () => {
    if (!dupMatch || !mergePreview) return;
    setError(null);
    setNotice(null);
    try {
      const patch: Record<string, string> = {};
      const changed: string[] = [];
      for (const row of mergePreview.rows) {
        if (mergeChecked[row.key]) {
          patch[row.key] = row.after;
          changed.push(row.label);
        }
      }
      // 미리보기 열 때 계산한 memo patch 를 그대로 적용 — 화면 표시(memoLine)와 저장값 일치.
      if (mergePreview.memoPatch) {
        patch.memo = mergePreview.memoPatch;
        changed.push("메모");
      }
      const done: string[] = [];
      if (Object.keys(patch).length) {
        await api(`/customers/${dupMatch.id}`, { method: "PATCH", body: JSON.stringify(patch) });
        done.push(`${changed.join(", ")} 갱신`);
      }
      // 주민번호는 형식 검증이 있어 실패할 수 있다. 별도 PATCH 로 보내서 실패해도
      // 위의 전화/주소 등 갱신은 살아남게 한다.
      let rrnError: string | null = null;
      const rc = rrnCheck(rrnInput);
      if (rrnEnabled() && rc.digits.length > 0 && rc.ok) {
        try {
          await api(`/customers/${dupMatch.id}`, { method: "PATCH", body: JSON.stringify({ rrn: rc.digits }) });
          done.push("주민번호 갱신");
        } catch (re) {
          rrnError = `주민번호는 저장하지 못했습니다: ${re instanceof Error ? re.message : String(re)}`;
        }
      } else if (rrnEnabled() && rc.digits.length > 0) {
        rrnError = `주민번호를 저장하지 않았습니다 — ${rc.msg}`;
      }
      if (pendingConsult) {
        await api(`/customers/${dupMatch.id}/consultations`, {
          method: "POST",
          body: JSON.stringify(pendingConsult),
        });
        done.push("통화 상담 추가");
        setPendingConsult(null);
        setPendingCoverage([]);
      }
      const targetId = dupMatch.id;
      const targetName = dupMatch.name;
      setDupMatch(null);
      setMergePreview(null);
      const polMerge = await savePolicyFor(targetId);
      if (polMerge.saved) done.push(`보험계약 ${polMerge.saved}건 추가`);
      if (polMerge.failures.length)
        rrnError =
          (rrnError ? rrnError + " / " : "") +
          `계약 저장 실패 ${polMerge.failures.length}건:\n` +
          polMerge.failures.map((f) => `· ${f.label} — ${f.error}`).join("\n");
      const summary = done.length ? `${targetName}: ${done.join(", ")} 완료` : `${targetName}: 바뀐 내용이 없습니다`;
      if (screen === "new") {
        startNew(true);
        // startNew 가 비운 pending* 상태는 다음 렌더에야 상위로 올라간다(useEffect) — 바로 이어지는
        // onOpenCustomer 가 그 전에 실행되면 상위(App)가 방금 지운 초안을 "아직 있음"으로 오인해
        // 저장 직후에 불필요한 확인창을 띄운다. 여기서 먼저 명시적으로 내려보낸다.
        onDraftChange?.({ hasDraft: false, isAnalyzing: false });
        setNotice(summary);
        onOpenCustomer?.(targetId);
      } else {
        setMode("edit");
        setSelectedId(targetId);
        setNotice(summary);
      }
      if (rrnError) setError(rrnError);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const refreshList = useCallback(async () => {
    listAbortRef.current?.abort();
    const ac = new AbortController();
    listAbortRef.current = ac;
    const gen = ++listGenRef.current;
    try {
      const p = new URLSearchParams();
      if (debouncedQ.trim()) p.set("q", debouncedQ.trim());
      if (sort !== "name") p.set("sort", sort);
      if (filterExpiring) p.set("expiring_days", "30");
      if (filterFollowUp) p.set("has_follow_up", "true");
      if (filterTag) p.set("tag", filterTag);
      if (statusFilter) p.set("status", statusFilter);
      if (ownOnly) p.set("own", "1");
      const qs = p.toString();
      const customers = await api(`/customers${qs ? "?" + qs : ""}`, { signal: ac.signal });
      if (gen !== listGenRef.current) return;
      setList(customers.customers);
      const tags = await api("/tags", { signal: ac.signal });
      if (gen !== listGenRef.current) return;
      setAllTags(tags.tags);
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return;
      if (gen !== listGenRef.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [debouncedQ, sort, filterExpiring, filterFollowUp, filterTag, statusFilter, ownOnly]);

  // 만기 임박 / 생일 임박 보기: 좌측 목록을 GET /management 섹션으로 교체.
  const refreshMgmt = useCallback(async () => {
    if (listMode === "normal") return;
    try {
      const m = await api("/management");
      setMgmt({ expiring_policies: m.expiring_policies ?? [], birthdays: m.birthdays ?? [] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [listMode]);

  const reloadDetail = useCallback(async (id: string) => {
    const gen = ++detailGenRef.current;
    const d: CustomerDetail = await api(`/customers/${id}`);
    if (gen !== detailGenRef.current) return null;
    setDetail(d);
    return d;
  }, []);

  // 고객 상세를 열고 폼까지 채운다 (선택/포커스 양쪽에서 재사용)
  const openDetail = useCallback(
    async (id: string) => {
      const gen = detailGenRef.current + 1;
      try {
        const d = await reloadDetail(id);
        if (!d) return;
        setForm({
          name: d.name ?? "",
          phone: d.phone ?? "",
          birth_date: d.birth_date ?? "",
          gender: d.gender ?? "",
          email: d.email ?? "",
          address: d.address ?? "",
          occupation: d.occupation ?? "",
          tags: (d.tags ?? []).join(", "),
          customer_status: d.customer_status ?? "",
          first_registered_ym: d.first_registered_ym ?? "",
          memo: d.memo ?? "",
        });
        setRrnInput(d.rrn ?? "");   // 마스킹 없음 — 전체 값을 그대로 편집
        setPendingDelete(false);
        setPendingPolicyDelete(null);
        setPendingConsultDelete(null);
        setPendingPolicies([]);
        setPolicyChecked([]);
        setPendingDocType(undefined);
        setPendingCoverage([]);
        setPendingConsult(null);
        setMode("edit");
        setError(null);
        setNotice(null); // 고객을 정상적으로 열었으면 이전 안내/오류 문구 제거
      } catch (e) {
        if (gen !== detailGenRef.current) return;
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes("찾을 수 없") || msg.includes("404")) {
          // 삭제됐거나 사라진 고객 — 조용히 선택 해제
          detailGenRef.current++;
          setSelectedId(null);
          setDetail(null);
          setMode(screen === "new" ? "new" : "none");
          setNotice("선택한 고객을 찾을 수 없습니다 (삭제되었을 수 있어요).");
        } else {
          setError(msg);
        }
      }
    },
    [reloadDetail, screen],
  );

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), q ? 280 : 0);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  useEffect(() => () => listAbortRef.current?.abort(), []);

  useEffect(() => {
    refreshMgmt();
  }, [refreshMgmt]);

  // 상단 배너 등에서 "만기 임박 보기"로 진입
  useEffect(() => {
    if (focusListView) {
      setListMode(focusListView.view);
      onListViewConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusListView]);

  // 홈 대시보드 / 던져넣기 등에서 고객을 눌러 들어온 경우. 한 번 쓰고 App 에서 비운다
  // (그래야 탭을 다시 열 때 옛 참조로 없는 고객을 조회하지 않는다).
  useEffect(() => {
    if (focusCustomer) {
      setSelectedId(focusCustomer.id);
      onFocusConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusCustomer]);

  useEffect(() => {
    if (!selectedId) {
      detailGenRef.current++;
      setDetail(null);
      return;
    }
    openDetail(selectedId);
  }, [selectedId, openDetail]);

  useEffect(() => { setActiveTab("basic"); }, [selectedId]);

  const startNew = (skipDraftGuard = false) => {
    if (!skipDraftGuard && !canLeaveAnalysisDraft()) return;
    detailGenRef.current++;
    setSelectedId(null);
    setDetail(null);
    setForm(EMPTY_FORM);
    setRrnInput("");
    setIntakeText("");
    setBulkRows([]);
    setPendingConsult(null);
    setPendingPolicyDelete(null);
    setPendingConsultDelete(null);
    setPendingPolicies([]);
    setPolicyChecked([]);
    setPendingDocType(undefined);
    setPendingCoverage([]);
    setDupMatch(null);
    setMergePreview(null);
    setMode("new");
    setError(null);
    setNotice(null);
  };

  const nullifyBlanks = (o: Record<string, string>) =>
    Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.trim() === "" ? null : v.trim()]));

  // 주민번호 형식 즉시 검사 (백엔드 rrn.normalize 와 같은 규칙). 하이픈·공백은 무시.
  const rrnCheck = (v: string): { digits: string; ok: boolean; msg: string } => {
    const d = v.replace(/\D/g, "");
    if (d.length === 0) return { digits: "", ok: true, msg: "" };
    if (d.length !== 13) return { digits: d, ok: false, msg: `${d.length}자리 — 숫자 13자리를 입력하세요 (하이픈은 넣어도 됩니다)` };
    const mm = +d.slice(2, 4);
    const dd = +d.slice(4, 6);
    if (mm < 1 || mm > 12) return { digits: d, ok: false, msg: "월(3~4번째 자리)이 01~12가 아닙니다" };
    if (dd < 1 || dd > 31) return { digits: d, ok: false, msg: "일(5~6번째 자리)이 01~31이 아닙니다" };
    if (!"12345678".includes(d[6])) return { digits: d, ok: false, msg: "7번째 자리(성별)가 1~8이 아닙니다" };
    const cen = "1256".includes(d[6]) ? "19" : "20";
    const g = "1357".includes(d[6]) ? "남" : "여";
    const derived = `${cen}${d.slice(0, 2)}-${d.slice(2, 4)}-${d.slice(4, 6)}`;
    return { digits: d, ok: true, msg: `형식 OK — 저장하면 생년월일 ${derived} · ${g} 로 맞춰집니다` };
  };

  const renderRow = (c: Customer) => {
    const exp = ddayLabel(c.soonest_expiry);
    const fu = ddayLabel(c.next_follow_up);
    return (
      <div
        key={c.id}
        onClick={() => {
          if (!canOpenCustomer(c.id)) return;
          setMode("edit");
          setSelectedId(c.id);
        }}
        style={{
          padding: "8px 10px",
          cursor: "pointer",
          borderBottom: "1px solid #eee",
          background: c.id === selectedId ? "#eef4ff" : undefined,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {c.name}
            {statusChip(c.effective_status)}
          </span>
          <span style={{ display: "flex", gap: 4 }}>
            {fu && <span style={{ ...badge(fu.color), marginLeft: 0 }}>연 {fu.label}</span>}
            {exp && <span style={{ ...badge(exp.color), marginLeft: 0 }}>만 {exp.label}</span>}
          </span>
        </div>
        <div style={{ fontSize: 12, color: "#888" }}>
          {c.phone || "-"}
          {c.active_policy_count > 0 && ` · 계약 ${c.active_policy_count}`}
        </div>
        {c.tags.length > 0 && (
          <div style={{ fontSize: 11, color: "#2563eb" }}>{c.tags.map((t) => `#${t}`).join(" ")}</div>
        )}
      </div>
    );
  };

  // 문서에서 추출한 계약(들)을 고객 가입목록에 등록. 고객 저장은 유지한다.
  const savePolicyFor = async (cid: string) => {
    if (!pendingPolicies.length) return { saved: 0, failures: [] };
    const r = await savePolicies(api, cid, pendingPolicies, policyChecked);
    if (r.saved) {
      const failedIdx = new Set(r.failures.map((f) => f.index));
      setPendingPolicies((prev) => prev.filter((_, i) => failedIdx.has(i)));
      setPolicyChecked((prev) => prev.filter((_, i) => failedIdx.has(i)));
    }
    return r;
  };

  const saveCustomer = async () => {
    setError(null);
    setNotice(null);
    try {
      const payload: Record<string, unknown> = nullifyBlanks(form);
      payload.tags = form.tags.split(",").map((t) => t.trim()).filter(Boolean);
      // 상태: 빈 값이면 아예 안 보낸다 (신규는 서버가 '가망'; PATCH 에 null 을 보내면 기존 값이 지워짐).
      // '가입'은 자동 파생값이라 select 에 없음 → 여기 올 수 없음.
      if (!form.customer_status) delete payload.customer_status;

      // 주민번호는 절대 본 payload 에 섞지 않는다. 형식 검증(422)이 나머지 필드 저장까지
      // 막지 않도록 별도 PATCH 로 뒤이어 보낸다. 13자리가 아니면 아예 보내지 않는다.
      const rc = rrnCheck(rrnInput);
      const rrnDigits = rc.digits;
      let rrnSaved = false;
      let rrnError: string | null = null;
      const saveRrn = async (cid: string) => {
        if (!rrnEnabled()) return; // 파일럿: 주민번호 입력 기능 OFF
        if (rrnDigits.length === 0) return;
        if (!rc.ok) {
          rrnError = `주민등록번호를 저장하지 않았습니다 — ${rc.msg}`;
          return;
        }
        try {
          await api(`/customers/${cid}`, { method: "PATCH", body: JSON.stringify({ rrn: rrnDigits }) });
          rrnSaved = true;
        } catch (re) {
          rrnError = `주민번호는 저장하지 못했습니다: ${re instanceof Error ? re.message : String(re)}`;
        }
      };

      if (mode === "new") {
        const created: Customer = await api("/customers", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        track("customer_created", { via: "form" });
        await saveRrn(created.id);
        // 녹취로 만든 경우: 그 통화를 첫 상담 이력으로 붙인다
        if (pendingConsult) {
          try {
            await api(`/customers/${created.id}/consultations`, {
              method: "POST",
              body: JSON.stringify(pendingConsult),
            });
            setPendingConsult(null);
            setPendingCoverage([]);
          } catch (e) {
            setNotice("고객은 등록됐지만 상담 이력 저장에 실패했습니다: " + String(e));
          }
        }
        const pol = await savePolicyFor(created.id);
        await refreshList();
        if (rrnError) {
          // 고객은 만들어졌지만 주민번호 형식이 틀림 — 화면을 떠나지 말고 고치게 한다.
          setSelectedId(created.id);
          setMode("edit");
          setNotice(`${created.name} 등록됨 (아래에서 주민번호만 고쳐 저장하세요)`);
        } else if (screen === "new") {
          startNew(true);
          onDraftChange?.({ hasDraft: false, isAnalyzing: false }); // 위 confirmMerge 와 동일한 이유
          setNotice(
            `${created.name} 등록 완료` +
              (rrnSaved ? " · 주민번호 저장됨" : "") +
              (pol.saved ? ` · 보험계약 ${pol.saved}건 추가` : ""),
          );
          onOpenCustomer?.(created.id);
        } else {
          setSelectedId(created.id);
        }
        if (pol.failures.length)
          setError(
            `계약 저장 실패 ${pol.failures.length}건:\n` +
              pol.failures.map((f) => `· ${f.label} — ${f.error}`).join("\n"),
          );
      } else if (selectedId) {
        await api(`/customers/${selectedId}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        track("customer_updated", { fields_changed: Object.keys(payload).length });
        await saveRrn(selectedId);
        let consultSaved = false;
        if (pendingConsult) {
          await api(`/customers/${selectedId}/consultations`, {
            method: "POST",
            body: JSON.stringify(pendingConsult),
          });
          setPendingConsult(null);
          setPendingCoverage([]);
          consultSaved = true;
        }
        const pol = await savePolicyFor(selectedId);
        await refreshList();
        if (selectedIdRef.current !== selectedId) return;
        const fresh = await reloadDetail(selectedId);
        if (!fresh) return;
        setRrnInput(fresh.rrn ?? "");   // 저장된 주민번호를 다시 칸에 채워 확인시킨다
        // 주민번호를 저장하면 서버가 생년월일·성별을 거기에 맞춰 갱신한다 → 폼도 다시 맞춘다
        setForm((prev) => ({
          ...prev,
          birth_date: fresh.birth_date ?? prev.birth_date,
          gender: fresh.gender ?? prev.gender,
          customer_status: fresh.customer_status ?? prev.customer_status,
        }));
        const done: string[] = [];
        if (rrnSaved && fresh.birth_date) done.push(`주민번호 갱신됨 (생년월일 ${fresh.birth_date}로 맞춤)`);
        else if (rrnSaved) done.push("주민번호 갱신됨");
        if (consultSaved) done.push("보장분석 상담 1건 추가");
        if (pol.saved) done.push(`보험계약 ${pol.saved}건 추가`);
        if (!done.length && !rrnError) done.push("저장됨");
        setNotice(done.join(" · "));
        if (pol.failures.length)
          setError(
            `계약 저장 실패 ${pol.failures.length}건:\n` +
              pol.failures.map((f) => `· ${f.label} — ${f.error}`).join("\n"),
          );
      }
      if (rrnError) setError(rrnError);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // 추출 결과(items[]) 를 화면에 반영: 0명 → 안내, 1명 → 폼에 채움, 2명+ → 일괄 목록.
  const applyItems = (items: any[]) => {
    const note: string[] = [];

    if (items.length === 0) {
      setNotice([...note, "인식된 고객이 없습니다. 내용을 확인하세요."].join(" · "));
    } else if (items.length === 1) {
      const it = items[0];
      const f = it.fields as Record<string, string | null>;
      setForm((prev) => ({
        ...prev,
        name: f.name ?? prev.name,
        phone: f.phone ?? prev.phone,
        birth_date: f.birth_date ?? prev.birth_date,
        gender: f.gender ?? prev.gender,
        email: f.email ?? prev.email,
        address: f.address ?? prev.address,
        occupation: f.occupation ?? prev.occupation,
        memo: f.memo ?? prev.memo,
      }));
      if (f.rrn) setRrnInput(f.rrn);
      if (it.consultation || it.coverage_status?.length) {
        setPendingConsult({
          ...(it.consultation ?? {
            consulted_at: null,
            channel: "보장분석",
            title: "보장분석 검토",
            content: null,
            transcript: null,
            follow_up_at: null,
          }),
          ...(it.coverage_status?.length
            ? { coverage_json: JSON.stringify(it.coverage_status) }
            : {}),
        });
      }
      if (it.policies?.length) {
        setPendingPolicies(it.policies);
        setPolicyChecked(it.policies.map((p: PolicyDraft) => !!(p.insurer || p.product_name)));
        setPendingDocType(it.doc_type);
      }
      if (it.coverage_status?.length) setPendingCoverage(it.coverage_status);
      if (it.warnings?.length) note.push("확인 필요: " + it.warnings.join(" / "));
      if (it.match) setDupMatch({ id: it.match.id, name: it.match.name, reason: it.match_reason });
      if (note.length) setNotice(note.join(" · "));
    } else {
      if (note.length) setNotice(note.join(" · "));
      setBulkRows(
        items.map((it) => ({
          ...it,
          name: it.fields?.name ?? "",
          phone: it.fields?.phone ?? "",
          action: it.match ? "merge" : "new",
        })),
      );
    }
  };

  // 붙여넣은 텍스트: 1명이면 아래 폼에 채우고, 2명 이상이면 일괄 목록을 띄운다.
  const parseIntake = async () => {
    if (!intakeText.trim()) return;
    setIntakeBusy(true);
    setError(null);
    setNotice(null);
    setBulkRows([]);
    setDupMatch(null);
    setMergePreview(null);
    try {
      const r = await api("/customers/intake/bulk", {
        method: "POST",
        body: JSON.stringify({ text: intakeText }),
      });
      applyItems((r.items ?? []) as any[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIntakeBusy(false);
    }
  };

  // 끌어다 놓거나 [파일 선택] 으로 받은 파일: 녹취·PDF·텍스트 무엇이든 /capture 로 분석.
  const analyzeFile = async (f: File | null) => {
    if (!f) return;
    const isAudio = /\.(m4a|mp3|wav|aac|aiff?|ogg|oga|flac|opus|wma|amr|3gp|mp4|mov|m4b|webm)$/i.test(f.name);
    setAudioIntakeBusy(true);
    setError(null);
    setNotice(null);
    setBulkRows([]);
    setDupMatch(null);
    setMergePreview(null);
    setFileKind(isAudio ? "audio" : "doc");
    try {
      const fd = new FormData();
      fd.append("file", f);
      if (intakeText.trim()) fd.append("text", intakeText.trim());
      if (intakeContext.trim()) fd.append("context", intakeContext.trim());
      const b = await api("/capture", { method: "POST", body: fd });
      applyItems((b.items ?? []) as any[]);
      setIntakeContext(""); // 분석에 반영됐으니 비운다
      setStagedIntake(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAudioIntakeBusy(false);
      setFileKind(null);
    }
  };

  const pickIntakeFile = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,.m4a,.mp3,.wav,.aac,.aiff,.pdf,.txt,.csv,.md,text/plain";
    input.onchange = () => setStagedIntake(input.files?.[0] ?? null); // 바로 분석 안 함
    input.click();
  };

  const patchRow = (i: number, patch: Record<string, unknown>) =>
    setBulkRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const runBulk = async () => {
    setError(null);
    setNotice(null);
    setBulkBusy(true);
    const res = { created: 0, merged: 0, skipped: 0, failed: 0 };
    let lastErr: string | null = null;
    // 주민번호는 던져넣기와 동일하게 항상 별도 PATCH (본 payload 와 분리).
    const saveRrn = async (cid: string, rd: string) => {
      if (!rrnEnabled()) return; // 파일럿: 주민번호 입력 기능 OFF
      if (rd.length !== 13) return;
      try {
        await api(`/customers/${cid}`, { method: "PATCH", body: JSON.stringify({ rrn: rd }) });
      } catch (e) {
        lastErr = "주민번호 저장 실패: " + (e instanceof Error ? e.message : String(e));
      }
    };
    for (const row of bulkRows) {
      if (row.action === "skip") {
        res.skipped++;
        continue;
      }
      try {
        const f = row.fields ?? {};
        const rd = String(row.rrn ?? f.rrn ?? "").replace(/\D/g, "");
        if (row.action === "merge" && row.match) {
          const ex = row.match;
          const patch: Record<string, string> = {};
          // 사용자가 행에서 보는 이름·전화만 덮어쓰고, 안 보이는 필드는 빈 칸만 채운다.
          for (const k of ["name", "phone"]) {
            const v = String(k === "phone" ? row.phone : row.name).trim();
            if (v && v !== (ex[k] || "")) patch[k] = v;
          }
          for (const k of ["birth_date", "gender", "email", "address", "occupation"]) {
            const v = String(f[k] || "").trim();
            if (v && !(ex[k] || "").trim()) patch[k] = v;
          }
          if (Object.keys(patch).length)
            await api(`/customers/${ex.id}`, { method: "PATCH", body: JSON.stringify(patch) });
          await saveRrn(ex.id, rd);
          if (row.consultation)
            await api(`/customers/${ex.id}/consultations`, { method: "POST", body: JSON.stringify(row.consultation) });
          res.merged++;
        } else {
          const payload: Record<string, unknown> = {
            name: row.name?.trim() || f.name || "이름미상",
            phone: row.phone?.trim() || null,
            birth_date: f.birth_date || null,
            gender: f.gender || null,
            email: f.email || null,
            address: f.address || null,
            occupation: f.occupation || null,
            memo: f.memo || null,
          };
          const created = await api("/customers", { method: "POST", body: JSON.stringify(payload) });
          await saveRrn(created.id, rd);
          if (row.consultation)
            await api(`/customers/${created.id}/consultations`, { method: "POST", body: JSON.stringify(row.consultation) });
          res.created++;
        }
      } catch (e) {
        res.failed++;
        lastErr = lastErr || (e instanceof Error ? e.message : String(e));
      }
    }
    setBulkBusy(false);
    setBulkRows([]);
    await refreshList();
    setNotice(
      `일괄 처리 완료 — 신규 ${res.created} · 보완 ${res.merged} · 건너뜀 ${res.skipped}` +
        (res.failed ? ` · 실패 ${res.failed}` : ""),
    );
    if (lastErr) setError(lastErr);
  };

  const clearRrn = async () => {
    if (!selectedId) return;
    const startId = selectedId;
    setError(null);
    try {
      await api(`/customers/${startId}`, { method: "PATCH", body: JSON.stringify({ rrn: "" }) });
      if (selectedIdRef.current !== startId) return;
      setRrnInput("");
      await reloadDetail(startId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteCustomer = async () => {
    if (!selectedId) return;
    setError(null);
    try {
      await api(`/customers/${selectedId}`, { method: "DELETE" });
      track("customer_deleted", {});
      setPendingDelete(false);
      detailGenRef.current++;
      setSelectedId(null);
      setForm(EMPTY_FORM);
      setRrnInput("");
      setMode(screen === "new" ? "new" : "none");
      await refreshList();
      setNotice("고객이 삭제되었습니다.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const wrap: Wrap = (fn, rethrow = false) => async (...args) => {
    const startId = selectedId;
    setError(null);
    try {
      await fn(...args);
      if (startId && selectedIdRef.current === startId) {
        const fresh = await reloadDetail(startId);
        // 계약 추가/삭제로 서버가 상태(가망↔가입)를 자동 조정했을 수 있다.
        // 폼의 상태값을 서버 값과 다시 맞춰야 위 [변경사항 저장]이 옛 값을 되돌리지 않는다.
        if (fresh) setForm((prev) => ({ ...prev, customer_status: fresh.customer_status ?? "" }));
      }
      // 계약·상담·약관 변경이 좌측 목록(상태 칩·계약 수·만기 배지·만기/생일 보기)에도 반영되게.
      await refreshList();
      if (listMode !== "normal") await refreshMgmt();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (rethrow) throw e;
    }
  };

  const kpi = mode === "edit" && detail ? {
    policyCount: detail.policies.length,
    activeCount: detail.policies.filter((p) => p.status === "ACTIVE").length,
    premium: totalMonthlyPremium(detail.policies),
    expiring: expiringCount(detail.policies, 30),
  } : null;

  return (
    <div style={{ display: "flex", gap: 16, padding: 16, maxWidth: 1040, margin: "0 auto" }}>
      {/* 왼쪽: 고객 목록 (고객 목록 탭에서만) */}
      {screen === "list" && (
      <div style={{ width: 260, flexShrink: 0 }}>
        <button onClick={() => startNew()} style={{ width: "100%", marginBottom: 6 }}>
          새 고객
        </button>
        <input
          style={{ ...inputStyle, marginBottom: 6 }}
          placeholder="이름 / 전화 검색"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {/* 가입/미가입/해지 세그먼트 — GET /customers?status= */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 6 }}>
          {[
            ["", "전체"],
            ["가입", "가입"],
            ["미가입", "미가입"],
            ["가망", "가망"],
            ["해지", "해지"],
          ].map(([v, label]) => (
            <button
              key={v || "all"}
              onClick={() => setStatusFilter(v)}
              style={{
                flex: "1 1 auto",
                fontSize: 12,
                padding: "4px 6px",
                border: "1px solid " + (statusFilter === v ? "#2563eb" : "#ccc"),
                background: statusFilter === v ? "#2563eb" : "#fff",
                color: statusFilter === v ? "#fff" : "#333",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
          <select
            style={{ ...inputStyle, flex: 1 }}
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="name">가나다순</option>
            <option value="recent">최근 상담순</option>
            <option value="expiry">만기 빠른순</option>
            <option value="follow_up">후속 임박순</option>
            <option value="birthday">생일순</option>
          </select>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6, fontSize: 12 }}>
          <label>
            <input type="checkbox" checked={filterExpiring} onChange={(e) => setFilterExpiring(e.target.checked)} />
            만기 30일
          </label>
          <label>
            <input type="checkbox" checked={filterFollowUp} onChange={(e) => setFilterFollowUp(e.target.checked)} />
            후속 있음
          </label>
          <label title="내가 직접 가입시킨 계약이 있는 고객만">
            <input
              type="checkbox"
              checked={ownOnly}
              onChange={(e) => {
                setOwnOnly(e.target.checked);
                localStorage.setItem("iai.own_only", e.target.checked ? "1" : "0");
              }}
            />
            내 계약만
          </label>
          {allTags.length > 0 && (
            <select
              style={{ fontSize: 12, maxWidth: 110 }}
              value={filterTag}
              onChange={(e) => setFilterTag(e.target.value)}
            >
              <option value="">태그 전체</option>
              {allTags.map((t) => (
                <option key={t} value={t}>
                  #{t}
                </option>
              ))}
            </select>
          )}
        </div>
        {/* 만기 임박 / 생일 임박 보기 전환 */}
        <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
          {(
            [
              ["expiry", "만기 임박"],
              ["birthday", "생일 임박"],
            ] as ["expiry" | "birthday", string][]
          ).map(([v, label]) => (
            <button
              key={v}
              onClick={() => setListMode((m) => (m === v ? "normal" : v))}
              style={{
                flex: 1,
                fontSize: 12,
                padding: "4px 6px",
                border: "1px solid " + (listMode === v ? "#c60" : "#ccc"),
                background: listMode === v ? "#fff3e0" : "#fff",
                color: listMode === v ? "#8a4b00" : "#333",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              {listMode === v ? `▸ ${label}` : label}
            </button>
          ))}
        </div>

        <div style={{ border: "1px solid #ddd", borderRadius: 6, maxHeight: 560, overflowY: "auto" }}>
          {listMode === "expiry" ? (
            !mgmt ? (
              <p style={{ padding: 8, color: "#888" }}>불러오는 중…</p>
            ) : mgmt.expiring_policies.length === 0 ? (
              <p style={{ padding: 8, color: "#888" }}>30일 내 만기 계약이 없습니다.</p>
            ) : (
              mgmt.expiring_policies.map((p: any, i: number) => (
                <div
                  key={p.id ?? i}
                  onClick={() => {
                    if (!canOpenCustomer(p.customer_id)) return;
                    setMode("edit");
                    setSelectedId(p.customer_id);
                  }}
                  style={{ padding: "8px 10px", cursor: "pointer", borderBottom: "1px solid #eee" }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                    <span>{p.customer_name ?? "(고객)"}</span>
                    {daysBadge(p.days)}
                  </div>
                  <div style={{ fontSize: 12, color: "#888" }}>
                    {p.insurer || ""} {p.product_name || ""} · {p.end_date}
                    {p.end_date_derived === 0 ? " (직접)" : p.end_date_derived === 1 ? " (자동)" : ""}
                  </div>
                </div>
              ))
            )
          ) : listMode === "birthday" ? (
            !mgmt ? (
              <p style={{ padding: 8, color: "#888" }}>불러오는 중…</p>
            ) : mgmt.birthdays.length === 0 ? (
              <p style={{ padding: 8, color: "#888" }}>30일 내 생일인 고객이 없습니다.</p>
            ) : (
              mgmt.birthdays.map((b: any) => (
                <div
                  key={b.id}
                  onClick={() => {
                    if (!canOpenCustomer(b.id)) return;
                    setMode("edit");
                    setSelectedId(b.id);
                  }}
                  style={{ padding: "8px 10px", cursor: "pointer", borderBottom: "1px solid #eee" }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                    <span>
                      {b.name}
                      {b.estimated && (
                        <span style={{ ...badge("#888"), marginLeft: 6 }}>추정</span>
                      )}
                    </span>
                    {daysBadge(b.days_until)}
                  </div>
                  <div style={{ fontSize: 12, color: "#888" }}>
                    {(b.birth_date || "").slice(5) || "-"} · 만 {b.turning_age}세
                  </div>
                </div>
              ))
            )
          ) : (
            <>
              {list.length === 0 && <p style={{ padding: 8, color: "#888" }}>고객이 없습니다.</p>}
              {sort === "name"
                ? GROUP_ORDER.filter((g) => list.some((c) => chosungOf(c.name) === g)).map((g) => (
                    <div key={g}>
                      <div style={{ position: "sticky", top: 0, background: "#eef1f5", padding: "2px 10px", fontSize: 12, fontWeight: 700, color: "#555" }}>
                        {g}
                      </div>
                      {list.filter((c) => chosungOf(c.name) === g).map(renderRow)}
                    </div>
                  ))
                : list.map(renderRow)}
            </>
          )}
        </div>
      </div>
      )}

      {/* 오른쪽 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {error && <p style={{ color: "#b00", whiteSpace: "pre-line" }}>오류: {error}</p>}
        {notice && (
          <p style={{ background: "#fff7e6", color: "#8a4b00", padding: "6px 10px", borderRadius: 6, fontSize: 13 }}>
            {notice}
          </p>
        )}
        {mode === "none" && <p style={{ color: "#888" }}>왼쪽에서 고객을 고르거나 "새 고객"을 누르세요.</p>}

        {(mode === "new" || mode === "edit") && (
          <>
            <div
              style={{
                position: "sticky",
                top: 40,
                zIndex: 5,
                background: "#fff",
                borderBottom: "1px solid #e5e7eb",
                padding: mode === "edit" && detail ? "8px 0 0" : "8px 0",
                marginBottom: 8,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", width: "100%" }}>
                <strong style={{ fontSize: 15 }}>{mode === "new" ? "새 고객 등록" : (detail?.name || "고객 정보")}</strong>
                {mode === "edit" && detail && statusChip(detail.effective_status)}
                <button
                  onClick={saveCustomer}
                  style={{ fontWeight: 700, background: "#2563eb", color: "#fff", border: "none", borderRadius: 5, padding: "6px 16px", cursor: "pointer" }}
                >
                  {mode === "new" ? "등록" : "변경사항 저장"}
                </button>
                {mode === "edit" && !pendingDelete && (
                  <button onClick={() => setPendingDelete(true)} style={{ color: "#b00", fontSize: 12 }}>
                    고객 삭제
                  </button>
                )}
                {mode === "edit" && pendingDelete && (
                  <span style={{ fontSize: 12, color: "#b00" }}>
                    보험계약·상담 이력까지 삭제됩니다.{" "}
                    <button onClick={deleteCustomer} style={{ color: "#fff", background: "#b00", border: "none", borderRadius: 4, padding: "2px 8px" }}>
                      삭제 확인
                    </button>{" "}
                    <button onClick={() => setPendingDelete(false)}>취소</button>
                  </span>
                )}
              </div>
              {mode === "edit" && detail && kpi && (
                <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "2px 12px", fontSize: 12, color: "#555", width: "100%" }}>
                  <span>전화 <b style={{ color: "#333" }}>{detail.phone || "-"}</b></span>
                  <span>계약 <b style={{ color: "#333" }}>{kpi.policyCount}</b>건{kpi.policyCount !== kpi.activeCount && <> (유지중 <b style={{ color: "#333" }}>{kpi.activeCount}</b>)</>}</span>
                  <span>총 월납 <b style={{ color: "#333" }}>₩{kpi.premium.total.toLocaleString()}</b>{kpi.premium.lumpSumCount > 0 && <span style={{ color: "#888" }} title="일시납 보험료는 총 월납에서 제외됩니다"> +일시납 {kpi.premium.lumpSumCount}건</span>}</span>
                  <span style={{ color: kpi.expiring > 0 ? "#c60" : "#555" }}>만기 임박 <b style={{ color: "#333" }}>{kpi.expiring}</b>건 (30일)</span>
                </div>
              )}
              {mode === "edit" && detail && (
                <div role="tablist" style={{ display: "flex", flexWrap: "nowrap", overflowX: "auto", gap: 2, width: "100%" }}>
                  {([
                    ["basic", "기본정보", null],
                    ["policies", "계약", detail.policies.length],
                    ["consultations", "상담", detail.consultations.length],
                    ["coverage", "보장분석", null],
                    ["ask", "약관질문", null],
                    ["audit", "이력", null],
                  ] as [DetailTab, string, number | null][]).map(([tab, label, count]) => {
                    const on = activeTab === tab;
                    return (
                      <button key={tab} role="tab" id={`tab-${tab}`} aria-controls={`panel-${tab}`} aria-selected={on} tabIndex={on ? 0 : -1} onClick={() => setActiveTab(tab)} style={{ flex: "0 0 auto", background: "none", border: "none", borderBottom: "2px solid " + (on ? "#2563eb" : "transparent"), color: on ? "#2563eb" : "#555", fontWeight: on ? 700 : 400, fontSize: 13, padding: "6px 10px", cursor: "pointer", whiteSpace: "nowrap" }}>
                        {label}{count !== null && <span style={{ marginLeft: 4, fontSize: 12, fontWeight: 400, color: on ? "#2563eb" : "#888" }}>{count}</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div role="tabpanel" id="panel-basic" aria-labelledby="tab-basic" style={{ display: mode === "edit" && activeTab !== "basic" ? "none" : undefined }}>
            {mode === "new" && (
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  if (!audioIntakeBusy && !intakeBusy) setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (audioIntakeBusy || intakeBusy) return;
                  const f = e.dataTransfer.files?.[0] ?? null;
                  if (f) setStagedIntake(f); // 바로 분석 안 함 — 설명을 받는다
                }}
                style={{
                  marginBottom: 14,
                  padding: 10,
                  border: dragOver ? "2px dashed #2563eb" : "1px dashed #bbb",
                  borderRadius: 8,
                  background: dragOver ? "#eef4ff" : "#fbfbfd",
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600 }}>빠른 등록</div>
                {stagedIntake ? (
                  <div style={{ border: "1px solid #2563eb", borderRadius: 8, padding: 10, background: "#fff", marginTop: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                      <span style={{ fontWeight: 600, color: "#161" }}>📎 {stagedIntake.name}</span>
                      <button onClick={() => setStagedIntake(null)} disabled={audioIntakeBusy} style={{ fontSize: 12 }}>
                        파일 빼기
                      </button>
                    </div>
                    <textarea
                      style={{ ...inputStyle, minHeight: 72 }}
                      placeholder={
                        "이 파일에 대해 알려주세요 — 누구 자료인지(고객 이름), 어느 보험사, 어떤 문서(제안서·증권·보장분석·녹취)인지, 특별히 봐야 할 점.\n여기 적은 내용이 분석에 함께 들어갑니다. (저장은 안 됩니다)"
                      }
                      value={intakeContext}
                      onChange={(e) => setIntakeContext(e.target.value)}
                      autoFocus
                    />
                    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                      <button
                        onClick={() => analyzeFile(stagedIntake)}
                        disabled={audioIntakeBusy}
                        style={{ fontWeight: 700, background: "#2563eb", color: "#fff", border: "none", borderRadius: 5, padding: "6px 16px" }}
                      >
                        {audioIntakeBusy ? "분석 중…" : "이 파일 분석"}
                      </button>
                      <button onClick={() => { setStagedIntake(null); setIntakeContext(""); }} disabled={audioIntakeBusy}>
                        취소
                      </button>
                    </div>
                  </div>
                ) : (
                <>
                <div style={{ fontSize: 12, color: "#888", margin: "4px 0 6px" }}>
                  붙여넣고 [분석] (여러 명이면 한 줄에 한 명씩), 또는 <b>녹취·PDF·텍스트 파일을 이 영역에
                  끌어다 놓기</b> · [파일 선택] 버튼도 가능. (파일은 끌어다 놓은 뒤 설명을 적고 분석)
                </div>
                <textarea
                  style={{ ...inputStyle, minHeight: 84 }}
                  placeholder={"박준서 010-8765-4321 850315-1234567 자영업\n김민지 010-1234-5678 900101-2345678 주부"}
                  value={intakeText}
                  onChange={(e) => setIntakeText(e.target.value)}
                />
                <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                  <button onClick={parseIntake} disabled={intakeBusy || audioIntakeBusy}>
                    {intakeBusy ? "분석 중... (~30초)" : "분석"}
                  </button>
                  <button onClick={pickIntakeFile} disabled={intakeBusy || audioIntakeBusy}>
                    {audioIntakeBusy ? "분석 중…" : "📎 파일 선택 (녹취·PDF·텍스트)"}
                  </button>
                  {pendingConsult != null && (
                    <span style={{ fontSize: 12, color: "#161", alignSelf: "center" }}>
                      {(pendingConsult as { channel?: string }).channel === "가입제안서"
                        ? "가입제안서 검토 이력 1건이 함께 저장됩니다"
                        : "통화 1건이 첫 상담으로 함께 저장됩니다"}
                    </span>
                  )}
                </div>
                </>
                )}

                {(intakeBusy || audioIntakeBusy) && (
                  <div className="ai-analyzing" role="status" aria-live="polite">
                    <span style={{ fontSize: 16 }}>⏳</span>
                    <span>
                      {fileKind === "audio"
                        ? "🎙 녹취 파일 전사·분석 중…  통화 길이에 따라 1~2분 걸릴 수 있어요. 창을 닫지 마세요."
                        : fileKind === "doc"
                        ? "📄 파일에서 고객 정보 분석 중…"
                        : "텍스트 분석 중…"}
                    </span>
                  </div>
                )}

                {bulkRows.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ fontSize: 12, color: "#555", marginBottom: 4 }}>
                      {bulkRows.length}명 — 각 행의 동작을 확인하고 [일괄 실행]
                    </div>
                    {bulkRows.map((row, i) => (
                      <div
                        key={i}
                        style={{ display: "flex", gap: 4, alignItems: "center", padding: "4px 0", borderBottom: "1px solid #eee", fontSize: 13, flexWrap: "wrap" }}
                      >
                        <select value={row.action} onChange={(e) => patchRow(i, { action: e.target.value })} style={{ fontSize: 12 }}>
                          <option value="new">신규 등록</option>
                          {row.match && <option value="merge">기존 「{row.match.name}」 갱신</option>}
                          <option value="skip">건너뜀</option>
                        </select>
                        <input style={{ padding: 3, width: 90 }} value={row.name} onChange={(e) => patchRow(i, { name: e.target.value })} placeholder="이름" />
                        <input style={{ padding: 3, width: 120 }} value={row.phone} onChange={(e) => patchRow(i, { phone: e.target.value })} placeholder="전화" />
                        {row.match && (
                          <span style={{ fontSize: 11, color: "#8a4b00" }}>
                            {row.match_reason === "phone" ? "전화 일치" : row.match_reason === "name+birthdate" ? "동일인" : "동명"}
                          </span>
                        )}
                        {row.warnings?.length > 0 && (
                          <span style={{ fontSize: 11, color: "#b00" }} title={row.warnings.join(" / ")}>⚠</span>
                        )}
                      </div>
                    ))}
                    <button onClick={runBulk} disabled={bulkBusy} style={{ marginTop: 6, fontWeight: 600 }}>
                      {bulkBusy ? "처리 중..." : `${bulkRows.length}명 일괄 실행`}
                    </button>
                  </div>
                )}
                {dupMatch && (
                  <div style={{ marginTop: 8, background: "#fff7e6", color: "#8a4b00", padding: "8px 10px", borderRadius: 6, fontSize: 13 }}>
                    <div style={{ marginBottom: 6 }}>
                      {dupMatch.reason === "name+birthdate"
                        ? `이름·생년월일이 같은 「${dupMatch.name}」 고객이 이미 있습니다. 동일인입니다.`
                        : dupMatch.reason === "phone"
                        ? `같은 전화번호의 「${dupMatch.name}」 고객이 이미 있습니다.`
                        : `같은 이름의 「${dupMatch.name}」 고객이 있습니다 (동명이인일 수 있어요).`}
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <button onClick={openMergePreview}>
                        기존 「{dupMatch.name}」 정보 갱신
                      </button>
                      <button
                        onClick={() => {
                          setDupMatch(null);
                          setMergePreview(null);
                          setNotice("동명이인으로 처리합니다. 위 정보를 확인하고 [등록]을 누르세요.");
                        }}
                      >
                        다른 사람 → 새로 등록
                      </button>
                    </div>
                    {mergePreview && (
                      <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid #e8cf9a" }}>
                        <div style={{ marginBottom: 6 }}>
                          「{dupMatch.name}」에 아래 내용을 반영합니다. 체크 해제한 항목은 기존 값을 유지합니다.
                        </div>
                        {mergePreview.rows.length > 0 ? (
                          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12, color: "#5f3b00" }}>
                            <thead>
                              <tr>
                                {['필드', '기존 값', '새 값', '적용'].map((v) => (
                                  <th key={v} style={{ border: "1px solid #e8cf9a", padding: "4px 6px", textAlign: "left" }}>{v}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {mergePreview.rows.map((row) => (
                                <tr key={row.key}>
                                  <td style={{ border: "1px solid #e8cf9a", padding: "4px 6px" }}>{row.label}</td>
                                  <td style={{ border: "1px solid #e8cf9a", padding: "4px 6px" }}>{row.before || "(없음)"}</td>
                                  <td style={{ border: "1px solid #e8cf9a", padding: "4px 6px" }}>{row.after || "(없음)"}</td>
                                  <td style={{ border: "1px solid #e8cf9a", padding: "4px 6px" }}>
                                    <input
                                      type="checkbox"
                                      checked={mergeChecked[row.key] ?? false}
                                      onChange={(e) => setMergeChecked((c) => ({ ...c, [row.key]: e.target.checked }))}
                                    />
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ) : (
                          <div style={{ fontSize: 12 }}>갱신할 고객 정보가 없습니다</div>
                        )}
                        {mergePreview.memoLine && (
                          <div style={{ marginTop: 6, fontSize: 12 }}>메모에 추가됨: {mergePreview.memoLine}</div>
                        )}
                        {(mergePreview.policyCount > 0 || mergePreview.hasConsult) && (
                          <div style={{ marginTop: 4, fontSize: 12 }}>
                            계약 {mergePreview.policyCount}건·상담 {mergePreview.hasConsult ? 1 : 0}건도 함께 추가됩니다
                          </div>
                        )}
                        {mergePreview.hasRrn && (
                          <div style={{ marginTop: 4, fontSize: 12 }}>주민번호도 갱신됩니다</div>
                        )}
                        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                          {(mergePreview.rows.length > 0 ||
                            mergePreview.memoPatch ||
                            mergePreview.hasConsult ||
                            mergePreview.policyCount > 0 ||
                            mergePreview.hasRrn) && <button onClick={confirmMerge}>선택 항목 적용</button>}
                          <button onClick={() => setMergePreview(null)}>취소</button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {hasPendingDraftNow() && (
              <div style={{ fontSize: 12, color: "#8a4b00", marginBottom: 6 }}>
                미리보기입니다 — 아래 [{mode === "new" ? "등록" : "변경사항 저장"}] 을 눌러야 저장됩니다.
              </div>
            )}
            {pendingPolicies.length > 0 && (
              <PolicyList
                policies={pendingPolicies}
                checked={policyChecked}
                docType={pendingDocType}
                onToggle={(pi, v) =>
                  setPolicyChecked((cs) => cs.map((c, j) => (j === pi ? v : c)))
                }
                onEdit={(pi, patch) =>
                  setPendingPolicies((ps) => ps.map((p, j) => (j === pi ? { ...p, ...patch } : p)))
                }
              />
            )}
            {pendingCoverage.length > 0 && (
              <CoverageTableEditable
                rows={pendingCoverage}
                onCommit={(newRows) => {
                  setPendingCoverage(newRows);
                  setPendingConsult((prev) =>
                    prev
                      ? { ...prev, coverage_json: newRows.length ? JSON.stringify(newRows) : null }
                      : prev,
                  );
                }}
              />
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, maxWidth: 540 }}>
              {(
                [
                  ["name", "이름 *"],
                  ["phone", "전화"],
                  ["birth_date", "생년월일 (YYYY-MM-DD)"],
                  ["gender", "성별 (M/F)"],
                  ["email", "이메일"],
                  ["address", "주소"],
                  ["occupation", "직업"],
                  ["first_registered_ym", "최초 등록 년월 (예: 2024-03)"],
                ] as [keyof Form, string][]
              ).map(([key, label]) => (
                <label key={key} style={{ fontSize: 13 }}>
                  {label}
                  <input
                    style={inputStyle}
                    value={form[key]}
                    onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                    onBlur={key === "birth_date" ? () => {
                      const n = normalizeDate(form.birth_date, { birth: true });
                      setBirthDateHint(n === null);
                      if (n !== null) setForm({ ...form, birth_date: n });
                    } : undefined}
                  />
                  {key === "birth_date" && birthDateHint && (
                    <span style={{ color: "#b00", fontSize: 11 }}>올바른 날짜를 입력하세요.</span>
                  )}
                </label>
              ))}
              <label style={{ fontSize: 13, gridColumn: "1 / span 2" }}>
                상태{" "}
                <span style={{ color: "#888", fontWeight: 400 }}>
                  (직접 선택. 계약 추가하면 '가입', 전 계약 해지되면 '해지'로 자동 보정)
                </span>
                <select
                  style={inputStyle}
                  value={form.customer_status}
                  onChange={(e) => setForm({ ...form, customer_status: e.target.value })}
                >
                  <option value="">{mode === "new" ? "가망 (기본)" : "미지정"}</option>
                  <option value="가입">가입</option>
                  <option value="가망">가망</option>
                  <option value="미가입">미가입</option>
                  <option value="해지">해지</option>
                </select>
              </label>
              <label style={{ fontSize: 13, gridColumn: "1 / span 2" }}>
                태그 <span style={{ color: "#888", fontWeight: 400 }}>(콤마로 구분: VIP, 암보험관심)</span>
                <input
                  style={inputStyle}
                  value={form.tags}
                  onChange={(e) => setForm({ ...form, tags: e.target.value })}
                />
              </label>
              {rrnEnabled() && (
              <label style={{ fontSize: 13, gridColumn: "1 / span 2" }}>
                주민등록번호{" "}
                <span style={{ color: "#888", fontWeight: 400 }}>
                  (앞 6자리 생년월일 + 성별 1자리 + 뒤 6자리. 하이픈 무시. 암호화 저장)
                </span>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    style={inputStyle}
                    placeholder={detail?.has_rrn ? "새 번호를 입력하면 갱신됩니다" : "예: 901010-1234567"}
                    value={rrnInput}
                    onChange={(e) => setRrnInput(e.target.value)}
                  />
                  {mode === "edit" && detail?.has_rrn && (
                    <button type="button" onClick={clearRrn} style={{ color: "#b00" }}>
                      삭제
                    </button>
                  )}
                </div>
                {rrnInput.replace(/\D/g, "").length > 0 && (
                  <span
                    style={{
                      fontSize: 11,
                      color: rrnCheck(rrnInput).ok ? "#1a7a2e" : "#b00",
                    }}
                  >
                    {rrnCheck(rrnInput).ok ? "✓ " : "⚠ "}
                    {rrnCheck(rrnInput).msg}
                  </span>
                )}
              </label>
              )}
              <label style={{ fontSize: 13, gridColumn: "1 / span 2" }}>
                메모
                <textarea
                  style={{ ...inputStyle, minHeight: 44 }}
                  value={form.memo}
                  onChange={(e) => setForm({ ...form, memo: e.target.value })}
                />
              </label>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <button onClick={saveCustomer} style={{ fontWeight: 600 }}>
                {mode === "new" ? "등록" : "변경사항 저장"}
              </button>
            </div>
            </div>
          </>
        )}

        {mode === "edit" && detail && (
          <>
            {/* key={detail.id}: 고객을 바꾸면 내부 입력 상태(작성 중 계약·스테이징된 녹취 파일)를 초기화한다 */}
            <div role="tabpanel" id="panel-policies" aria-labelledby="tab-policies" style={{ display: activeTab === "policies" ? undefined : "none" }}>
              <PoliciesSection
                key={detail.id}
                detail={detail}
                wrap={wrap}
                pendingPolicyDelete={pendingPolicyDelete}
                setPendingPolicyDelete={setPendingPolicyDelete}
              />
            </div>
            <div role="tabpanel" id="panel-consultations" aria-labelledby="tab-consultations" style={{ display: activeTab === "consultations" ? undefined : "none" }}>
              <ConsultationsSection
                key={detail.id}
                detail={detail}
                wrap={wrap}
                pendingConsultDelete={pendingConsultDelete}
                setPendingConsultDelete={setPendingConsultDelete}
              />
            </div>
            <div role="tabpanel" id="panel-coverage" aria-labelledby="tab-coverage" style={{ display: activeTab === "coverage" ? undefined : "none" }}>
              <CoveragePanel
                key={detail.id}
                customerId={detail.id}
                policyCount={detail.policies.length}
                consultations={detail.consultations}
                wrap={wrap}
              />
            </div>
            <div role="tabpanel" id="panel-ask" aria-labelledby="tab-ask" style={{ display: activeTab === "ask" ? undefined : "none" }}>
              <AskPanel key={detail.id} customerId={detail.id} hasDocs={detail.policies.some((p) => p.document_id)} />
            </div>
            <div role="tabpanel" id="panel-audit" aria-labelledby="tab-audit" style={{ display: activeTab === "audit" ? undefined : "none" }}>
              <AuditPanel key={detail.id} customerId={detail.id} active={activeTab === "audit"} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PoliciesSection({
  detail,
  wrap,
  pendingPolicyDelete,
  setPendingPolicyDelete,
}: {
  detail: CustomerDetail;
  wrap: Wrap;
  pendingPolicyDelete: string | null;
  setPendingPolicyDelete: (id: string | null) => void;
}) {
  const [form, setForm] = useState<PolicyForm>(EMPTY_POLICY);
  const [editId, setEditId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<PolicyForm>(EMPTY_POLICY);
  const [editOrigEnd, setEditOrigEnd] = useState(""); // 편집 시작 시점의 만기일 (안 건드렸는지 판단용)
  const [dateHints, setDateHints] = useState<Record<string, boolean>>({});

  const toBody = (f: PolicyForm) => ({
    ...Object.fromEntries(
      Object.entries(f)
        .filter(([k]) => k !== "premium" && k !== "is_own")
        .map(([k, v]) => [k, (v as string).trim() === "" ? null : (v as string).trim()]),
    ),
    premium: f.premium.trim() === "" ? null : Number(f.premium),
    is_own: f.is_own,
  });

  const MEANINGFUL: (keyof PolicyForm)[] = [
    "insurer", "product_name", "policy_number", "premium",
    "start_date", "end_date", "insured_period", "payment_period", "memo",
  ];

  const addPolicy = wrap(async () => {
    const body = toBody(form) as Record<string, unknown>;
    if (!MEANINGFUL.some((k) => String(body[k] ?? "").trim())) {
      throw new Error("보험사나 상품명을 입력한 뒤 [계약 추가]를 누르세요.");
    }
    await api(`/customers/${detail.id}/policies`, { method: "POST", body: JSON.stringify(body) });
    setForm(EMPTY_POLICY);
  });

  const startEdit = (p: Policy) => {
    setEditId(p.id);
    setEditOrigEnd(p.end_date ?? "");
    setEditForm({
      insurer: p.insurer ?? "",
      product_name: p.product_name ?? "",
      policy_number: p.policy_number ?? "",
      plan_type: p.plan_type ?? "",
      premium: p.premium != null ? String(p.premium) : "",
      payment_cycle: p.payment_cycle ?? "MONTHLY",
      start_date: p.start_date ?? "",
      end_date: p.end_date ?? "",
      insured_period: p.insured_period ?? "",
      payment_period: p.payment_period ?? "",
      status: p.status,
      memo: p.memo ?? "",
      is_own: p.is_own !== false,
      policyholder_name: p.policyholder_name ?? "",
      policyholder_rel: p.policyholder_rel ?? "",
    });
  };

  const saveEdit = wrap(async () => {
    if (editId) {
      const body: Record<string, unknown> = toBody(editForm);
      // 만기일을 건드리지 않았으면 아예 안 보낸다 → 보험기간 변경 시 서버가 자동 재계산할 수 있게.
      // (만기일을 직접 넣으면 end_date_derived=0 으로 고정되어 재계산이 막힘)
      if (editForm.end_date.trim() === editOrigEnd.trim()) delete body.end_date;
      await api(`/policies/${editId}`, { method: "PATCH", body: JSON.stringify(body) });
    }
    setEditId(null);
  });

  const attachDoc = (pid: string) =>
    wrap(async () => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "application/pdf,.pdf";
      const file: File | null = await new Promise((resolve) => {
        input.onchange = () => resolve(input.files?.[0] ?? null);
        input.click();
      });
      if (!file) return;
      const note = window.prompt("이 약관 파일 메모 (선택) — 계약 메모에 덧붙습니다", "");
      const fd = new FormData();
      fd.append("file", file);
      if (note && note.trim()) fd.append("note", note.trim());
      await api(`/policies/${pid}/document`, { method: "POST", body: fd });
    })();

  const field = (
    f: PolicyForm,
    set: (v: PolicyForm) => void,
    key: Exclude<keyof PolicyForm, "is_own">, // is_own 은 체크박스로 별도 렌더
    label: string,
    width = 110,
    isDate = false,
    birth = false,
  ) => (
    <label style={{ fontSize: 12 }}>
      {label}
      <input
        style={{ ...inputStyle, width }}
        value={f[key]}
        onChange={(e) => set({ ...f, [key]: e.target.value })}
        onBlur={isDate ? () => {
          const n = normalizeDate(f[key], { birth });
          setDateHints((h) => ({ ...h, [key]: n === null }));
          if (n !== null) set({ ...f, [key]: n });
        } : undefined}
      />
      {isDate && dateHints[key] && (
        <span style={{ display: "block", color: "#b00", fontSize: 11 }}>올바른 날짜를 입력하세요.</span>
      )}
    </label>
  );

  // 계약자(피보험자와 다를 때) 이름 + 관계. 이름 비우면 본인계약.
  const holderFields = (f: PolicyForm, set: (v: PolicyForm) => void) => (
    <>
      <label style={{ fontSize: 12 }}>
        계약자 (피보험자와 다르면)
        <input
          style={{ ...inputStyle, width: 120 }}
          value={f.policyholder_name}
          onChange={(e) => set({ ...f, policyholder_name: e.target.value })}
        />
      </label>
      <label style={{ fontSize: 12 }}>
        관계
        <select
          style={{ ...inputStyle, width: 100 }}
          value={f.policyholder_rel}
          onChange={(e) => set({ ...f, policyholder_rel: e.target.value })}
        >
          {POLICYHOLDER_RELS.map((r) => (
            <option key={r} value={r}>
              {r || "—"}
            </option>
          ))}
        </select>
      </label>
    </>
  );

  return (
    <div style={{ marginTop: 22 }}>
      <h3>
        보험계약 ({detail.policies.length})
        <span style={{ fontSize: 12, fontWeight: 400, color: "#666", marginLeft: 8 }}>
          이 고객 상태: {detail.effective_status || detail.customer_status || "가망"}
          {detail.policies.length === 0 && " (계약을 추가하면 '가입'으로 자동 변경)"}
        </span>
      </h3>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 13, width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
              {["보험사", "상품명", "증권번호", "월납", "만기", "상태", "약관", ""].map((h) => (
                <th key={h} style={{ padding: 4 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {detail.policies.map((p) =>
              editId === p.id ? (
                <tr key={p.id} style={{ background: "#fafafa" }}>
                  <td colSpan={8} style={{ padding: 6 }}>
                   <div
                    style={{
                      background: "#fff7e6",
                      borderLeft: "3px solid #f59e0b",
                      borderRadius: 4,
                      padding: "8px 10px",
                    }}
                   >
                    <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>
                      ✏️ 계약 수정 중 — {p.insurer || p.product_name || "(제목 없음)"}
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "end" }}>
                      {field(editForm, setEditForm, "insurer", "보험사")}
                      {field(editForm, setEditForm, "product_name", "상품명", 140)}
                      {field(editForm, setEditForm, "policy_number", "증권번호")}
                      {field(editForm, setEditForm, "premium", "월납(원)", 90)}
                      {field(editForm, setEditForm, "start_date", "시작일", 100, true)}
                      {field(editForm, setEditForm, "insured_period", "보험기간", 90)}
                      {field(editForm, setEditForm, "payment_period", "납입기간", 90)}
                      {field(editForm, setEditForm, "end_date", "만기일(비우면 없음)", 130, true)}
                      {holderFields(editForm, setEditForm)}
                      <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4, paddingBottom: 4 }}>
                        <input
                          type="checkbox"
                          checked={editForm.is_own}
                          onChange={(e) => setEditForm({ ...editForm, is_own: e.target.checked })}
                        />
                        내 계약
                      </label>
                      <label style={{ fontSize: 12 }}>
                        상태
                        <select
                          style={{ ...inputStyle, width: 110 }}
                          value={editForm.status}
                          onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}
                        >
                          {["ACTIVE", "LAPSED", "EXPIRED", "CANCELLED"].map((s) => (
                            <option key={s} value={s}>
                              {STATUS_LABEL[s] ?? s}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button onClick={saveEdit}>저장</button>
                      <button onClick={() => {
                        setEditId(null);
                        setPendingPolicyDelete(null);
                      }}>취소</button>
                    </div>
                   </div>
                  </td>
                </tr>
              ) : (
                <tr key={p.id} style={{ borderBottom: "1px solid #eee" }}>
                  <td style={{ padding: 4 }}>{p.insurer || "-"}</td>
                  <td style={{ padding: 4 }}>
                    {p.product_name || "-"}
                    {p.is_own ? (
                      <span style={{ marginLeft: 6, fontSize: 10, color: "#1a7a2e", background: "#e7f6e9", borderRadius: 4, padding: "1px 5px" }}>
                        내 계약
                      </span>
                    ) : p.is_own === false ? (
                      <span style={{ marginLeft: 6, fontSize: 10, color: "#888", background: "#eee", borderRadius: 4, padding: "1px 5px" }}>
                        타사/미확인
                      </span>
                    ) : null}
                    {p.policyholder_name ? (
                      <span style={{ marginLeft: 6, fontSize: 10, color: "#7c3aed", background: "#f1e9fe", borderRadius: 4, padding: "1px 5px" }}>
                        계약자: {p.policyholder_name}
                        {p.policyholder_rel ? ` (${p.policyholder_rel})` : ""}
                      </span>
                    ) : null}
                  </td>
                  <td style={{ padding: 4 }}>{p.policy_number || "-"}</td>
                  <td style={{ padding: 4 }}>{p.premium != null ? p.premium.toLocaleString() : "-"}</td>
                  <td style={{ padding: 4, whiteSpace: "nowrap" }}>
                    {p.end_date || "-"}
                    {expiryBadge(p.end_date)}
                    {p.end_date && (
                      <span style={{ color: "#888", fontSize: 11 }}>
                        {" "}
                        {p.end_date_derived === 1 ? "(자동)" : p.end_date_derived === 0 ? "(직접)" : ""}
                      </span>
                    )}
                    {p.payment_end_date && (
                      <div style={{ color: "#888", fontSize: 11 }}>납입종료 {p.payment_end_date}</div>
                    )}
                    {(() => {
                      const pct = policyProgressPct(p.start_date, p.end_date);
                      if (pct == null) return null;
                      const color = pct >= 90 ? "#b00" : pct >= 70 ? "#c60" : "#9aa4b2";
                      return (
                        <div title={`가입일~만기 진행 ${pct}%`} style={{ marginTop: 3, height: 4, background: "#eef1f5", borderRadius: 2, width: 90 }}>
                          <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 2 }} />
                        </div>
                      );
                    })()}
                  </td>
                  <td style={{ padding: 4 }}>{STATUS_LABEL[p.status] ?? p.status}</td>
                  <td style={{ padding: 4, whiteSpace: "nowrap" }}>
                    {p.document_id ? (
                      <>
                        <span style={{ color: "#161" }}>연결됨</span>{" "}
                        <button
                          style={{ color: "#b00" }}
                          onClick={wrap(async () => {
                            await api(`/policies/${p.id}/document`, { method: "DELETE" });
                          })}
                        >
                          해제
                        </button>
                      </>
                    ) : (
                      <button onClick={() => attachDoc(p.id)}>약관 PDF 연결</button>
                    )}
                  </td>
                  <td style={{ padding: 4, whiteSpace: "nowrap" }}>
                    <button onClick={() => startEdit(p)}>수정</button>{" "}
                    {pendingPolicyDelete === p.id ? (
                      <span style={{ fontSize: 12, color: "#b00" }}>
                        삭제할까요?{" "}
                        <button
                          style={{ color: "#fff", background: "#b00", border: "none", borderRadius: 4, padding: "2px 8px" }}
                          onClick={wrap(async () => {
                            setPendingPolicyDelete(null);
                            await api(`/policies/${p.id}`, { method: "DELETE" });
                          })}
                        >확인</button>{" "}
                        <button onClick={() => setPendingPolicyDelete(null)}>취소</button>
                      </span>
                    ) : (
                      <button style={{ color: "#b00" }} onClick={() => setPendingPolicyDelete(p.id)}>삭제</button>
                    )}
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>

      <div
        style={{
          marginTop: 10,
          background: "#eef7ff",
          borderLeft: "3px solid #2563eb",
          borderRadius: 4,
          padding: "8px 10px",
        }}
      >
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>➕ 새 계약 추가</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "end" }}>
        {field(form, setForm, "insurer", "보험사")}
        {field(form, setForm, "product_name", "상품명", 140)}
        {field(form, setForm, "policy_number", "증권번호")}
        {field(form, setForm, "premium", "월납(원)", 90)}
        {field(form, setForm, "start_date", "가입일", 100, true)}
        {field(form, setForm, "insured_period", "보험기간", 90)}
        {field(form, setForm, "payment_period", "납입기간", 90)}
        {field(form, setForm, "end_date", "만기일(비우면 자동)", 130, true)}
        {holderFields(form, setForm)}
        <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4, paddingBottom: 4 }}>
          <input
            type="checkbox"
            checked={form.is_own}
            onChange={(e) => setForm({ ...form, is_own: e.target.checked })}
          />
          내 계약
        </label>
        <button onClick={addPolicy}>계약 추가</button>
      </div>
      <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>
        입력하고 <b>[계약 추가]</b>를 누르면 이 계약만 바로 저장됩니다 — 위 <b>[변경사항 저장]</b>과 별개입니다.
        <br />
        보험기간(100세 등 나이형은 생년월일만으로, 20년 등은 가입일까지 있으면)·납입기간을 넣으면 만기일이 자동 계산됩니다.
      </div>
      </div>
    </div>
  );
}

function ConsultationsSection({
  detail,
  wrap,
  pendingConsultDelete,
  setPendingConsultDelete,
}: {
  detail: CustomerDetail;
  wrap: Wrap;
  pendingConsultDelete: string | null;
  setPendingConsultDelete: (id: string | null) => void;
}) {
  const [form, setForm] = useState<ConsultForm>(EMPTY_CONSULT);
  const [audioBusy, setAudioBusy] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [staged, setStaged] = useState<File | null>(null); // 업로드 대기 중인 녹취 파일
  const [audioNote, setAudioNote] = useState("");
  const [audioDate, setAudioDate] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [dateHints, setDateHints] = useState<Record<string, boolean>>({});

  const add = wrap(async () => {
    const body = Object.fromEntries(
      Object.entries(form).map(([k, v]) => [k, v.trim() === "" ? null : v.trim()]),
    );
    await api(`/customers/${detail.id}/consultations`, { method: "POST", body: JSON.stringify(body) });
    setForm(EMPTY_CONSULT);
  });

  const AUDIO_RE = /\.(m4a|mp3|wav|aac|aiff)$/i;
  const isAudio = (f: File) => f.type.startsWith("audio/") || AUDIO_RE.test(f.name);

  const pickAudio = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,.m4a,.mp3,.wav,.aac,.aiff";
    const file: File | null = await new Promise((resolve) => {
      input.onchange = () => resolve(input.files?.[0] ?? null);
      input.click();
    });
    if (file) {
      setAudioError(null);
      setStaged(file);
    }
  };

  const clearStaged = () => {
    setStaged(null);
    setAudioNote("");
    setAudioDate("");
  };

  const runUpload = async () => {
    if (!staged) return;
    setAudioBusy(true);
    setAudioError(null);
    try {
      const fd = new FormData();
      fd.append("file", staged);
      fd.append("channel", "전화 녹취");
      if (audioNote.trim()) fd.append("note", audioNote.trim());
      if (audioDate.trim()) fd.append("consulted_at", audioDate.trim());
      await api(`/customers/${detail.id}/consultations/from-audio`, { method: "POST", body: fd });
      clearStaged();
      await wrap(async () => {})(); // 상세 새로고침
    } catch (e) {
      setAudioError(e instanceof Error ? e.message : String(e));
    } finally {
      setAudioBusy(false);
    }
  };

  return (
    <div style={{ marginTop: 24 }}>
      <h3 style={{ display: "flex", alignItems: "center", gap: 10 }}>
        상담 이력 ({detail.consultations.length})
        <button onClick={pickAudio} disabled={audioBusy} style={{ fontSize: 13, fontWeight: 400 }}>
          {audioBusy ? "🎙 전사·요약 중... (1~2분)" : "🎙 녹취 파일 선택"}
        </button>
      </h3>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!audioBusy) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (audioBusy) return;
          const f = e.dataTransfer.files?.[0] ?? null;
          if (!f) return;
          if (!isAudio(f)) {
            setAudioError("녹취(오디오) 파일만 여기에 놓으세요");
            return;
          }
          setAudioError(null);
          setStaged(f);
        }}
        style={{
          margin: "6px 0 10px",
          padding: 10,
          border: dragOver ? "2px dashed #2563eb" : "1px dashed #bbb",
          borderRadius: 8,
          background: dragOver ? "#eef4ff" : "#fbfbfd",
          fontSize: 12,
          color: "#666",
        }}
      >
        {staged ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-start" }}>
            <div style={{ width: "100%", color: "#161", fontWeight: 600 }}>🎙 {staged.name}</div>
            <label style={{ flex: 1, minWidth: 220 }}>
              이 녹취 메모 (누구와의 통화인지, 어떤 상황인지)
              <textarea
                style={{ ...inputStyle, minHeight: 44 }}
                value={audioNote}
                onChange={(e) => setAudioNote(e.target.value)}
              />
            </label>
            <label>
              녹취 날짜 (선택)
              <input
                style={{ ...inputStyle, width: 130 }}
                placeholder="YYYY-MM-DD"
                value={audioDate}
                onChange={(e) => setAudioDate(e.target.value)}
              />
            </label>
            <div style={{ width: "100%", display: "flex", gap: 6 }}>
              <button onClick={runUpload} disabled={audioBusy} style={{ fontWeight: 600 }}>
                {audioBusy ? "전사·저장 중…" : "전사·저장"}
              </button>
              <button onClick={clearStaged} disabled={audioBusy}>취소</button>
            </div>
          </div>
        ) : (
          <span>녹취(오디오) 파일을 여기에 끌어다 놓거나 위 [녹취 파일 선택] 버튼을 쓰세요.</span>
        )}
      </div>

      {audioError && <p style={{ color: "#b00" }}>녹취 처리 오류: {audioError}</p>}
      <div style={{ border: "1px solid #eee", borderRadius: 6 }}>
        {detail.consultations.length === 0 && (
          <p style={{ padding: 8, color: "#888" }}>상담 기록이 없습니다.</p>
        )}
        {detail.consultations.map((k) => (
          <div key={k.id} style={{ display: "flex", borderBottom: "1px solid #eee" }}>
            <div style={{ width: 28, flexShrink: 0, position: "relative", display: "flex", justifyContent: "center" }}>
              <div style={{ position: "absolute", top: 0, bottom: 0, borderLeft: "1px solid #d7dce2" }} />
              <div style={{
                position: "relative",
                marginTop: 10,
                width: 16,
                height: 16,
                borderRadius: "50%",
                background: !k.follow_up_at ? "#9aa4b2" : k.follow_up_done_at ? "#2e9d50" : "#e89024",
                color: "#fff",
                fontSize: 10,
                lineHeight: "16px",
                textAlign: "center",
              }}>
                {k.follow_up_at ? (k.follow_up_done_at ? "✅" : "⏳") : ""}
              </div>
            </div>
            <div style={{ padding: "8px 10px 8px 4px", flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: "#888" }}>
                {(k.consulted_at || "").slice(0, 10)} · {k.channel ? `${channelIcon(k.channel)} ${k.channel}` : "-"}
                {k.follow_up_at && (
                  <>
                    {k.follow_up_done_at
                      ? ` · ✅ 후속 연락 완료 ${k.follow_up_done_at}`
                      : ` · ⏳ 후속 연락 ${k.follow_up_at}`}
                    {!k.follow_up_done_at && (() => {
                      const d = new Date((k.follow_up_at || "") + "T00:00:00");
                      if (Number.isNaN(d.getTime())) return null;
                      const days = Math.ceil((d.getTime() - Date.now()) / 86400000);
                      if (days < 0) return <span style={badge("#b00")}>연락 지연</span>;
                      if (days <= 7) return <span style={badge("#c60")}>D-{days}</span>;
                      return null;
                    })()}
                    {" "}
                    <button
                      style={{ fontSize: 12 }}
                      onClick={wrap(async () => {
                        if (k.follow_up_done_at) {
                          await api(`/consultations/${k.id}/follow-up/reopen`, { method: "POST" });
                        } else {
                          await api(`/consultations/${k.id}/follow-up/complete`, { method: "POST" });
                        }
                      })}
                    >
                      {k.follow_up_done_at ? "재개" : "완료"}
                    </button>
                  </>
                )}
              <span style={{ float: "right" }}>
                {pendingConsultDelete === k.id ? (
                  <span style={{ fontSize: 12, color: "#b00" }}>
                    삭제할까요?{" "}
                    <button
                      style={{ color: "#fff", background: "#b00", border: "none", borderRadius: 4, padding: "2px 8px" }}
                      onClick={wrap(async () => {
                        setPendingConsultDelete(null);
                        await api(`/consultations/${k.id}`, { method: "DELETE" });
                      })}
                    >확인</button>{" "}
                    <button onClick={() => setPendingConsultDelete(null)}>취소</button>
                  </span>
                ) : (
                  <button style={{ color: "#b00" }} onClick={() => setPendingConsultDelete(k.id)}>삭제</button>
                )}
              </span>
            </div>
            <div style={{ fontWeight: 600 }}>
              {k.transcript ? "🎙 " : ""}
              {k.title || "(제목 없음)"}
            </div>
            {k.content && <div style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{k.content}</div>}
            {k.transcript && (
              <details style={{ fontSize: 12, marginTop: 4 }}>
                <summary style={{ color: "#888" }}>전사 원문</summary>
                <div style={{ whiteSpace: "pre-wrap" }}>{k.transcript}</div>
              </details>
            )}
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "end" }}>
        <label style={{ fontSize: 12 }}>
          상담일
          <input
            style={{ ...inputStyle, width: 120 }}
            placeholder="YYYY-MM-DD"
            value={form.consulted_at}
            onChange={(e) => setForm({ ...form, consulted_at: e.target.value })}
            onBlur={() => {
              const n = normalizeDate(form.consulted_at);
              setDateHints((h) => ({ ...h, consulted_at: n === null }));
              if (n !== null) setForm({ ...form, consulted_at: n });
            }}
          />
          {dateHints.consulted_at && <span style={{ display: "block", color: "#b00", fontSize: 11 }}>올바른 날짜를 입력하세요.</span>}
        </label>
        <label style={{ fontSize: 12 }}>
          경로
          <select
            style={{ ...inputStyle, width: 90 }}
            value={form.channel}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          >
            {["방문", "전화", "온라인", "기타"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12, flex: 1, minWidth: 160 }}>
          제목
          <input
            style={inputStyle}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </label>
        <label style={{ fontSize: 12 }}>
          다음 연락일
          <input
            style={{ ...inputStyle, width: 120 }}
            placeholder="YYYY-MM-DD"
            value={form.follow_up_at}
            onChange={(e) => setForm({ ...form, follow_up_at: e.target.value })}
            onBlur={() => {
              const n = normalizeDate(form.follow_up_at);
              setDateHints((h) => ({ ...h, follow_up_at: n === null }));
              if (n !== null) setForm({ ...form, follow_up_at: n });
            }}
          />
          {dateHints.follow_up_at && <span style={{ display: "block", color: "#b00", fontSize: 11 }}>올바른 날짜를 입력하세요.</span>}
        </label>
        <label style={{ fontSize: 12, width: "100%" }}>
          내용
          <textarea
            style={{ ...inputStyle, minHeight: 40 }}
            value={form.content}
            onChange={(e) => setForm({ ...form, content: e.target.value })}
          />
        </label>
        <button onClick={add}>상담 기록 추가</button>
      </div>
    </div>
  );
}

type CoverageResult = {
  overall: string;
  categories: {
    name: string;
    status: string;
    detail: string;
    policies: string[];
    evidence_pages: number[];
  }[];
  gaps: string[];
  overlaps: string[];
  recommendations: string[];
  analyzed_policies: number;
  has_documents: boolean;
};

const STATUS_COLOR: Record<string, string> = {
  충분: "#161",
  중복: "#c60",
  부족: "#c60",
  없음: "#b00",
};

function AuditPanel({ customerId, active }: { customerId: string; active: boolean }) {
  const [items, setItems] = useState<AuditItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    api(`/customers/${customerId}/audit`)
      .then((rows) => { setItems(rows); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [customerId, active]);

  const actions: Record<string, string> = { create: "생성", update: "수정", delete: "삭제" };
  const entities: Record<string, string> = { customer: "고객", policy: "계약", consultation: "상담", import: "가져오기" };
  return (
    <div style={{ marginTop: 24, overflowX: "auto" }}>
      {error && <p style={{ color: "#b00" }}>오류: {error}</p>}
      {!error && items.length === 0 && <p style={{ color: "#888" }}>변경 이력이 없습니다</p>}
      {items.length > 0 && (
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
          <thead><tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th style={{ padding: 6 }}>시각</th><th style={{ padding: 6 }}>actor</th>
            <th style={{ padding: 6 }}>action</th><th style={{ padding: 6 }}>entity</th><th style={{ padding: 6 }}>fields</th>
          </tr></thead>
          <tbody>{items.map((item) => (
            <tr key={item.id} style={{ borderBottom: "1px solid #eee" }}>
              <td style={{ padding: 6, whiteSpace: "nowrap" }}>{new Date(item.created_at).toLocaleString()}</td>
              <td style={{ padding: 6 }}>{item.actor}</td><td style={{ padding: 6 }}>{actions[item.action] ?? item.action}</td>
              <td style={{ padding: 6 }}>{entities[item.entity] ?? item.entity}</td><td style={{ padding: 6 }}>{item.fields || "-"}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

function CoveragePanel({
  customerId,
  policyCount,
  consultations,
  wrap,
}: {
  customerId: string;
  policyCount: number;
  consultations: Consultation[];
  wrap: Wrap;
}) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<CoverageResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = consultations.find((c) => c.coverage_json);

  const run = async () => {
    setBusy(true);
    setError(null);
    setRes(null);
    try {
      setRes(await api(`/customers/${customerId}/coverage-analysis`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        marginTop: 24,
        padding: 12,
        border: "1px solid #ddd",
        borderRadius: 8,
        background: "#fbfbfd",
      }}
    >
      {latest && (
        <>
          <h4 style={{ marginTop: 0 }}>
            저장된 보장분석 — {latest.title || "(제목 없음)"} · {(latest.consulted_at || "").slice(0, 10)}
          </h4>
          <CoverageTableEditableSafe
            json={latest.coverage_json}
            onCommit={wrap(async (newRows: CoverageRow[]) => {
              await api(`/consultations/${latest.id}`, {
                method: "PATCH",
                body: JSON.stringify({ coverage_json: JSON.stringify(newRows) }),
              });
            }, true)}
          />
          <hr style={{ margin: "20px 0" }} />
        </>
      )}
      <h3 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 10 }}>
        보장 분석
        <button onClick={run} disabled={busy || policyCount === 0} style={{ fontSize: 13, fontWeight: 400 }}>
          {busy ? "분석 중... (~1분)" : "분석 실행"}
        </button>
      </h3>
      {policyCount === 0 && <p style={{ color: "#888", fontSize: 13 }}>보험계약을 먼저 등록하세요.</p>}
      {error && <p style={{ color: "#b00", whiteSpace: "pre-line" }}>오류: {error}</p>}

      {res && (
        <div style={{ fontSize: 13 }}>
          <p style={{ whiteSpace: "pre-wrap" }}>{res.overall}</p>
          <p style={{ color: "#888", fontSize: 12 }}>
            계약 {res.analyzed_policies}건 분석 · {res.has_documents ? "약관 근거 포함" : "약관 미첨부 (상품명 기준 개략)"}
          </p>

          {res.categories.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ borderCollapse: "collapse", width: "100%", marginTop: 6 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
                    <th style={{ padding: 4 }}>카테고리</th>
                    <th style={{ padding: 4 }}>상태</th>
                    <th style={{ padding: 4 }}>내용</th>
                  </tr>
                </thead>
                <tbody>
                  {res.categories.map((c, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #eee" }}>
                      <td style={{ padding: 4, whiteSpace: "nowrap" }}>{c.name}</td>
                      <td style={{ padding: 4, color: STATUS_COLOR[c.status] ?? "#333", fontWeight: 600 }}>
                        {c.status}
                      </td>
                      <td style={{ padding: 4 }}>
                        {c.detail}
                        {c.evidence_pages?.length > 0 && (
                          <span style={{ color: "#888" }}> (p.{c.evidence_pages.join(", ")})</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(["gaps", "overlaps", "recommendations"] as const).map((k) =>
            res[k].length > 0 ? (
              <div key={k} style={{ marginTop: 8 }}>
                <b>{k === "gaps" ? "보완 필요" : k === "overlaps" ? "중복 조정" : "제안"}</b>
                <ul style={{ margin: "4px 0" }}>
                  {res[k].map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}

function AskPanel({ customerId, hasDocs }: { customerId: string; hasDocs: boolean }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setError(null);
    setRes(null);
    try {
      setRes(await api(`/customers/${customerId}/ask?q=${encodeURIComponent(q.trim())}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: 24, padding: 12, border: "1px solid #ddd", borderRadius: 8, background: "#fbfbfd" }}>
      <h3 style={{ marginTop: 0 }}>이 고객 약관에 질문</h3>
      {!hasDocs && (
        <p style={{ color: "#888", fontSize: 13 }}>
          위 보험계약에 약관 PDF를 연결하면 그 약관들만 대상으로 질문할 수 있습니다.
        </p>
      )}
      <div style={{ display: "flex", gap: 6 }}>
        <input
          style={inputStyle}
          placeholder="예: 입원 시 하루 얼마 지급되나요?"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
        />
        <button onClick={ask} disabled={busy}>
          {busy ? "..." : "질문"}
        </button>
      </div>

      {error && <p style={{ color: "#b00" }}>오류: {error}</p>}

      {res && (
        <div style={{ marginTop: 10 }}>
          {res.disclaimer && res.basis === "policy_summary" && (
            <div style={{ background: "#fff8e1", borderLeft: "4px solid #f0a020", padding: "8px 10px", fontSize: 12, margin: "6px 0" }}>
              ⚠ {res.disclaimer}
            </div>
          )}
          <p style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{res.answer}</p>
          {res.basis === "clause" ? (
            <p style={{ fontSize: 12, color: "#555" }}>
              근거: {res.company ? `${res.company} / ` : ""}
              {res.clause || "조항 미상"}
              {res.page != null && ` · p.${res.page}`}
            </p>
          ) : res.basis === "policy_summary" ? (
            <p style={{ fontSize: 12, color: "#8a6500" }}>
              근거: 계약 요약정보 (약관 미연결) · 정확한 답변은 계약에 약관 PDF 를 연결하세요
            </p>
          ) : (
            <p style={{ fontSize: 12, color: "#b00" }}>근거를 찾지 못해 답변을 보류했습니다.</p>
          )}
          {res.sources?.length > 0 && (
            <details style={{ fontSize: 12 }}>
              <summary>참고 조각 {res.sources.length}개</summary>
              {res.sources.map((s, i) => (
                <div key={i} style={{ padding: "4px 0", borderBottom: "1px solid #eee" }}>
                  <div style={{ color: "#888" }}>
                    {s.filename} p.{s.page} · {s.score.toFixed(2)}
                  </div>
                  <div style={{ whiteSpace: "pre-wrap" }}>{s.text}</div>
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </div>
  );
}
