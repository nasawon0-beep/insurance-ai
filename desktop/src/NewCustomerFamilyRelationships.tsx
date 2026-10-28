import { useMemo, useState } from "react";
import type {
  FamilyApi,
  FamilyCustomerSummary,
  FamilyFormState,
  FamilyRegistrationDraft,
  FamilyRelationshipCode,
} from "./familyRelationshipModel";

const RELATIONS: Array<{ value: FamilyRelationshipCode; label: string }> = [
  { value: "SPOUSE", label: "배우자" },
  { value: "PARENT", label: "부모" },
  { value: "CHILD", label: "자녀" },
  { value: "SIBLING", label: "형제자매" },
  { value: "OTHER", label: "기타" },
];
const LABEL = Object.fromEntries(RELATIONS.map((item) => [item.value, item.label]));
const EMPTY: FamilyFormState = {
  relatedCustomerId: "",
  relationship: "",
  otherDescription: "",
  coParentCustomerId: "",
  sharedChildIds: [],
};
const inputStyle: React.CSSProperties = {
  width: "100%", minHeight: 38, padding: "7px 9px", boxSizing: "border-box",
  border: "1px solid var(--color-input-border)", borderRadius: 4,
  background: "var(--color-bg-surface)", color: "var(--color-text-primary)",
};

function meta(customer: FamilyCustomerSummary): string {
  return `${customer.birth_date ? `${customer.birth_date.slice(0, 4)}년생` : "생년 미입력"} · ${customer.phone || "전화 미입력"} · ID ${customer.id.slice(0, 8)}`;
}

export default function NewCustomerFamilyRelationships({
  customerName,
  api,
  drafts,
  onChange,
  disabled = false,
}: {
  customerName: string;
  api: FamilyApi;
  drafts: FamilyRegistrationDraft[];
  onChange: (next: FamilyRegistrationDraft[]) => void;
  disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<FamilyCustomerSummary[]>([]);
  const [form, setForm] = useState<FamilyFormState>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const selected = candidates.find((item) => item.id === form.relatedCustomerId) ?? null;
  const selectedIds = useMemo(() => new Set(drafts.map((item) => item.customer.id)), [drafts]);
  const spouseDrafts = drafts.filter((item) => item.form.relationship === "SPOUSE");
  const childDrafts = drafts.filter((item) => item.form.relationship === "CHILD");

  const resetEditor = () => {
    setEditing(false);
    setQuery("");
    setCandidates([]);
    setForm(EMPTY);
    setError(null);
  };
  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const response = await api(`/customers?q=${encodeURIComponent(query.trim())}&limit=20`);
      setCandidates(Array.isArray(response?.customers) ? response.customers : []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSearching(false);
    }
  };
  const add = () => {
    if (!selected || !form.relationship) {
      setError("기존 고객과 현재 등록할 고객 기준 관계를 선택해 주세요.");
      return;
    }
    if (selectedIds.has(selected.id)) {
      setError("같은 고객은 한 번만 선택할 수 있습니다.");
      return;
    }
    if (form.relationship === "OTHER" && !form.otherDescription.trim()) {
      setError("기타 관계 설명을 입력해 주세요.");
      return;
    }
    onChange([...drafts, { customer: selected, form }]);
    resetEditor();
  };

  return (
    <section data-testid="new-customer-family" style={{ marginTop: 12, border: "1px solid var(--color-border-default)", borderRadius: 7, background: "var(--color-bg-surface)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "11px 13px", borderBottom: expanded ? "1px solid var(--color-border-default)" : 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>{expanded ? "▾" : "▸"}</button>
          <strong>가족 연결</strong><span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>선택</span>
          <span style={{ fontWeight: 700 }}>{drafts.length}</span>
        </div>
        <button type="button" onClick={() => setEditing(true)} disabled={disabled}>+ 가족 선택</button>
      </div>
      {expanded && <div style={{ padding: 13 }}>
        <p style={{ margin: "0 0 9px", fontSize: 12, color: "var(--color-text-secondary)" }}>
          선택 항목이며 입력하지 않아도 고객을 등록할 수 있습니다. 기존 고객을 ID로 선택하며 계약·상담·분석 자료는 합치지 않습니다.
        </p>
        {drafts.map((draft) => <div key={draft.customer.id} style={{ display: "grid", gridTemplateColumns: "80px 1fr auto", gap: 9, alignItems: "center", padding: 9, marginTop: 6, background: "var(--color-bg-base)", borderRadius: 5 }}>
          <strong>{LABEL[draft.form.relationship as FamilyRelationshipCode]}</strong>
          <div><b>{draft.customer.name}</b><div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{meta(draft.customer)}{draft.form.otherDescription ? ` · ${draft.form.otherDescription}` : ""}</div></div>
          <button type="button" disabled={disabled} onClick={() => onChange(drafts.filter((item) => item.customer.id !== draft.customer.id))}>선택 취소</button>
        </div>)}
        {drafts.length === 0 && <div style={{ padding: 10, border: "1px dashed var(--color-border-hover)", borderRadius: 5, color: "var(--color-text-secondary)" }}>선택한 가족이 없습니다.</div>}

        {editing && <div style={{ marginTop: 11, padding: 12, border: "1px solid var(--color-border-default)", borderRadius: 6 }}>
          <strong style={{ display: "block", marginBottom: 7 }}>1. 기존 고객 검색</strong>
          <div style={{ display: "flex", gap: 7 }}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} placeholder="이름 또는 전화번호 검색" style={inputStyle} />
            <button type="button" onClick={() => void search()} disabled={searching}>{searching ? "검색 중…" : "검색"}</button>
          </div>
          {candidates.map((candidate) => {
            const duplicate = selectedIds.has(candidate.id);
            return <button type="button" key={candidate.id} disabled={duplicate} onClick={() => setForm((value) => ({ ...value, relatedCustomerId: candidate.id }))} style={{ width: "100%", marginTop: 5, display: "flex", justifyContent: "space-between", textAlign: "left" }}>
              <span><b>{candidate.name}</b><br /><span style={{ fontSize: 12 }}>{meta(candidate)}</span></span>
              <span>{duplicate ? "이미 선택됨" : form.relatedCustomerId === candidate.id ? "선택됨" : "ID 선택"}</span>
            </button>;
          })}
          <strong style={{ display: "block", margin: "12px 0 6px" }}>2. {customerName.trim() || "현재 등록할 고객"} 기준 관계</strong>
          <select value={form.relationship} onChange={(event) => setForm((value) => ({ ...value, relationship: event.target.value as FamilyRelationshipCode, otherDescription: "", coParentCustomerId: "", sharedChildIds: [] }))} style={inputStyle}>
            <option value="">관계를 선택하세요</option>
            {RELATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          {form.relationship === "OTHER" && <input maxLength={120} value={form.otherDescription} onChange={(event) => setForm((value) => ({ ...value, otherDescription: event.target.value }))} placeholder="기타 관계 설명 (한 줄, 120자 이내)" style={{ ...inputStyle, marginTop: 7 }} />}
          {form.relationship === "CHILD" && spouseDrafts.length > 0 && selected && <div style={{ marginTop: 8, padding: 9, background: "var(--color-accent-soft)" }}>
            <strong>공동 자녀 연결 (명시적 선택)</strong>
            {spouseDrafts.map((spouse) => <label key={spouse.customer.id} style={{ display: "block", marginTop: 5 }}><input type="radio" name="registration-co-parent" checked={form.coParentCustomerId === spouse.customer.id} onChange={() => setForm((value) => ({ ...value, coParentCustomerId: spouse.customer.id }))} /> 배우자 {spouse.customer.name} 고객의 자녀로도 연결</label>)}
            {form.coParentCustomerId && <button type="button" onClick={() => setForm((value) => ({ ...value, coParentCustomerId: "" }))}>선택 안 함</button>}
          </div>}
          {form.relationship === "SPOUSE" && childDrafts.length > 0 && <div style={{ marginTop: 8, padding: 9, background: "var(--color-accent-soft)" }}>
            <strong>기존 자녀 함께 연결 (명시적 선택)</strong>
            {childDrafts.map((child) => <label key={child.customer.id} style={{ display: "block", marginTop: 5 }}><input type="checkbox" checked={form.sharedChildIds.includes(child.customer.id)} onChange={(event) => setForm((value) => ({ ...value, sharedChildIds: event.target.checked ? [...value.sharedChildIds, child.customer.id] : value.sharedChildIds.filter((id) => id !== child.customer.id) }))} /> {child.customer.name} 고객을 새 배우자의 자녀로도 연결</label>)}
          </div>}
          {selected && <p style={{ fontSize: 12 }}>선택: {selected.name} · {meta(selected)}</p>}
          {error && <p role="alert" style={{ color: "var(--color-error)" }}>{error}</p>}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 7, marginTop: 10 }}><button type="button" onClick={resetEditor}>취소</button><button type="button" className="btn-primary" onClick={add}>선택 항목 추가</button></div>
        </div>}
      </div>}
    </section>
  );
}
