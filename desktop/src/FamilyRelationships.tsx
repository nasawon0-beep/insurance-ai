import { useCallback, useEffect, useMemo, useState } from "react";
import {
  eligibleSharedChildren,
  saveFamilyRelationship,
  spouseRelationships,
  type FamilyApi,
  type FamilyCustomerSummary,
  type FamilyFormState,
  type FamilyRelationship,
  type FamilyRelationshipCode,
} from "./familyRelationshipModel";

const RELATION_OPTIONS: { value: FamilyRelationshipCode; label: string }[] = [
  { value: "SPOUSE", label: "배우자" },
  { value: "PARENT", label: "부모" },
  { value: "CHILD", label: "자녀" },
  { value: "SIBLING", label: "형제자매" },
  { value: "OTHER", label: "기타" },
];

const EMPTY_FORM: FamilyFormState = {
  relatedCustomerId: "",
  relationship: "",
  otherDescription: "",
  coParentCustomerId: "",
  sharedChildIds: [],
};

const buttonSmall: React.CSSProperties = { fontSize: 12, minHeight: 32, padding: "4px 9px" };
const inputStyle: React.CSSProperties = {
  width: "100%", minHeight: 40, padding: "8px 10px", boxSizing: "border-box",
  border: "1px solid var(--color-input-border)", borderRadius: 4,
  background: "var(--color-bg-surface)", color: "var(--color-text-primary)",
};

function candidateMeta(customer: FamilyCustomerSummary): string {
  const birth = customer.birth_date ? `${customer.birth_date.slice(0, 4)}년생` : "생년 미입력";
  const phone = customer.phone || "전화 미입력";
  return `${birth} · ${phone}`;
}

export default function FamilyRelationships({
  customer,
  api,
  onOpenCustomer,
}: {
  customer: { id: string; name: string };
  api: FamilyApi;
  onOpenCustomer: (customer: FamilyCustomerSummary) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const [rows, setRows] = useState<FamilyRelationship[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<FamilyCustomerSummary[]>([]);
  const [searching, setSearching] = useState(false);
  const [form, setForm] = useState<FamilyFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api(`/customers/${customer.id}/family-relationships`);
      setRows(Array.isArray(response?.family_relationships) ? response.family_relationships : []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [api, customer.id]);

  useEffect(() => { void load(); }, [load]);

  const linkedIds = useMemo(() => new Set(rows.map((row) => row.customer.id)), [rows]);
  const spouses = spouseRelationships(rows);
  const existingChildren = eligibleSharedChildren(rows);
  const selected = candidates.find((candidate) => candidate.id === form.relatedCustomerId) ?? null;

  const resetDialog = () => {
    setDialogOpen(false);
    setQuery("");
    setCandidates([]);
    setForm(EMPTY_FORM);
    setError(null);
  };

  const openDialog = () => {
    setDialogOpen(true);
    setQuery("");
    setCandidates([]);
    setForm(EMPTY_FORM);
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

  const save = async () => {
    if (!form.relatedCustomerId || !form.relationship) {
      setError("연결할 고객과 현재 고객 기준 관계를 선택해 주세요.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await saveFamilyRelationship(api, customer.id, form, rows);
      setRows(next);
      resetDialog();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  };

  const unlink = async (row: FamilyRelationship) => {
    const confirmed = window.confirm(
      `${customer.name} 고객과 ${row.customer.name} 고객의 가족 관계만 해제할까요?\n` +
      "고객과 계약·상담·분석 자료는 유지되며, 다른 가족 관계는 삭제되지 않습니다.",
    );
    if (!confirmed) return;
    setError(null);
    try {
      await api(`/customers/${customer.id}/family-relationships/${row.id}`, { method: "DELETE" });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  return (
    <section style={{ background: "var(--color-bg-surface)", border: "1px solid var(--color-border-default)", borderRadius: 7, marginTop: 12 }} data-testid="family-section">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, padding: "12px 14px", borderBottom: expanded ? "1px solid var(--color-border-default)" : "none" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-label="가족 연결 목록 접기 또는 펼치기" style={{ ...buttonSmall, minWidth: 32 }}>{expanded ? "▾" : "▸"}</button>
          <strong style={{ fontSize: 15 }}>가족 연결</strong>
          <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>선택</span>
          <span style={{ minWidth: 24, height: 24, borderRadius: 12, display: "inline-flex", alignItems: "center", justifyContent: "center", background: "var(--color-accent-soft)", color: "var(--color-accent-text)", fontWeight: 700 }}>{rows.length}</span>
        </div>
        <button onClick={openDialog} className="btn-primary" style={buttonSmall}>+ 가족 추가</button>
      </div>
      {expanded && (
        <div style={{ padding: 14 }}>
          {loading ? <p style={{ color: "var(--color-text-secondary)" }}>가족 관계를 불러오는 중…</p> : rows.length === 0 ? (
            <div style={{ border: "1px dashed var(--color-border-hover)", borderRadius: 5, padding: 12, color: "var(--color-text-secondary)" }}>
              연결된 가족이 없습니다. 가족 연결은 고객 등록 필수 항목이 아닙니다.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {rows.map((row) => (
                <div key={row.id} style={{ display: "grid", gridTemplateColumns: "88px minmax(0,1fr) auto", alignItems: "center", gap: 10, padding: "9px 11px", background: "var(--color-bg-base)", border: "1px solid var(--color-border-default)", borderRadius: 5 }}>
                  <strong style={{ color: "var(--color-accent-text)" }}>{row.relationship_label}</strong>
                  <div>
                    <button onClick={() => onOpenCustomer(row.customer)} style={{ minHeight: 0, padding: 0, border: 0, background: "none", color: "var(--color-accent-text)", textDecoration: "underline", textUnderlineOffset: 3 }}>{row.customer.name}</button>
                    <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{candidateMeta(row.customer)}{row.other_description ? ` · ${row.other_description}` : ""}</div>
                  </div>
                  <button onClick={() => void unlink(row)} style={buttonSmall}>관계만 해제</button>
                </div>
              ))}
            </div>
          )}
          <p style={{ margin: "9px 0 0", fontSize: 12, color: "var(--color-text-secondary)" }}>이름을 누르면 상대 고객 상세로 이동합니다. 계약·상담·분석은 고객별로 분리됩니다.</p>
          {error && !dialogOpen && <p role="alert" style={{ color: "var(--color-error)", marginTop: 8 }}>{error}</p>}
        </div>
      )}

      {dialogOpen && (
        <div role="presentation" style={{ position: "fixed", inset: 0, zIndex: 9000, background: "var(--color-bg-overlay)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div role="dialog" aria-modal="true" aria-labelledby="family-dialog-title" style={{ width: "min(650px, 96vw)", maxHeight: "92vh", overflow: "auto", background: "var(--color-bg-surface)", border: "1px solid var(--color-border-default)", borderRadius: 8, boxShadow: "var(--shadow-md)" }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--color-border-default)" }}>
              <h2 id="family-dialog-title" style={{ margin: 0, fontSize: 19 }}>가족 연결 추가</h2>
              <p style={{ margin: "4px 0 0", color: "var(--color-text-secondary)", fontSize: 12 }}><b>{customer.name}</b> 고객 기준 관계입니다. 자동으로 다른 혈연을 추정하지 않습니다.</p>
            </div>
            <div style={{ padding: "16px 20px" }}>
              <label style={{ display: "block", fontWeight: 700, fontSize: 12, marginBottom: 5 }}>1. 기존 고객 검색</label>
              <div style={{ display: "flex", gap: 7 }}>
                <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} placeholder="이름 또는 전화번호 검색" style={inputStyle} />
                <button onClick={() => void search()} disabled={searching}>{searching ? "검색 중…" : "검색"}</button>
              </div>
              {candidates.length > 0 && (
                <div style={{ border: "1px solid var(--color-border-default)", borderRadius: 5, marginTop: 7, overflow: "hidden" }}>
                  {candidates.map((candidate) => {
                    const self = candidate.id === customer.id;
                    const duplicate = linkedIds.has(candidate.id);
                    const disabled = self || duplicate;
                    return (
                      <button key={candidate.id} disabled={disabled} onClick={() => setForm((value) => ({ ...value, relatedCustomerId: candidate.id }))} style={{ width: "100%", border: 0, borderBottom: "1px solid var(--color-border-default)", borderRadius: 0, display: "flex", justifyContent: "space-between", textAlign: "left", background: form.relatedCustomerId === candidate.id ? "var(--color-accent-soft)" : "var(--color-bg-surface)" }}>
                        <span><b>{candidate.name}</b> <span style={{ color: "var(--color-text-secondary)", fontSize: 12 }}>{candidateMeta(candidate)}</span></span>
                        <span style={{ fontSize: 12 }}>{self ? "자기 자신" : duplicate ? "이미 연결됨" : form.relatedCustomerId === candidate.id ? "선택됨" : "선택"}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              <label style={{ display: "block", fontWeight: 700, fontSize: 12, margin: "15px 0 5px" }}>2. {customer.name} 고객 기준 관계</label>
              <select value={form.relationship} onChange={(event) => setForm((value) => ({ ...value, relationship: event.target.value as FamilyRelationshipCode, otherDescription: "", coParentCustomerId: "", sharedChildIds: [] }))} style={inputStyle}>
                <option value="">관계를 선택하세요</option>
                {RELATION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>

              {form.relationship === "OTHER" && <input value={form.otherDescription} maxLength={120} onChange={(event) => setForm((value) => ({ ...value, otherDescription: event.target.value }))} placeholder="기타 관계 설명 (한 줄, 120자 이내)" style={{ ...inputStyle, marginTop: 8 }} />}

              {form.relationship === "CHILD" && spouses.length > 0 && selected && (
                <div style={{ marginTop: 9, padding: 11, background: "var(--color-accent-soft)", border: "1px solid var(--color-border-default)", borderRadius: 5 }}>
                  <strong style={{ display: "block", fontSize: 12, marginBottom: 6 }}>공동 자녀 연결 (명시적 선택)</strong>
                  {spouses.map((spouse) => (
                    <label key={spouse.id} style={{ display: "flex", gap: 7, alignItems: "flex-start" }}>
                      <input type="radio" name="co-parent" checked={form.coParentCustomerId === spouse.customer.id} onChange={() => setForm((value) => ({ ...value, coParentCustomerId: spouse.customer.id }))} />
                      배우자 {spouse.customer.name} 고객의 자녀로도 연결
                    </label>
                  ))}
                  {form.coParentCustomerId && <button onClick={() => setForm((value) => ({ ...value, coParentCustomerId: "" }))} style={{ ...buttonSmall, marginTop: 7 }}>선택 안 함</button>}
                  <p style={{ margin: "6px 0 0", fontSize: 12, color: "var(--color-text-secondary)" }}>저장 한 번으로 두 부모와 자녀의 반대쪽 관계까지 반영합니다.</p>
                </div>
              )}

              {form.relationship === "SPOUSE" && existingChildren.length > 0 && (
                <div style={{ marginTop: 9, padding: 11, background: "var(--color-accent-soft)", border: "1px solid var(--color-border-default)", borderRadius: 5 }}>
                  <strong style={{ display: "block", fontSize: 12, marginBottom: 6 }}>기존 자녀 함께 연결 (선택)</strong>
                  {existingChildren.map((child) => (
                    <label key={child.id} style={{ display: "flex", gap: 7, alignItems: "center", marginTop: 4 }}>
                      <input type="checkbox" checked={form.sharedChildIds.includes(child.customer.id)} onChange={(event) => setForm((value) => ({ ...value, sharedChildIds: event.target.checked ? [...value.sharedChildIds, child.customer.id] : value.sharedChildIds.filter((id) => id !== child.customer.id) }))} />
                      {child.customer.name} 고객을 새 배우자의 자녀로도 연결
                    </label>
                  ))}
                </div>
              )}

              {selected && <p style={{ margin: "9px 0 0", fontSize: 12, color: "var(--color-text-secondary)" }}>선택: {selected.name} · {candidateMeta(selected)}</p>}
              {error && <p role="alert" style={{ color: "var(--color-error)", marginTop: 9 }}>{error}</p>}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "12px 20px", borderTop: "1px solid var(--color-border-default)" }}>
              <button onClick={resetDialog} disabled={saving}>취소</button>
              <button className="btn-primary" onClick={() => void save()} disabled={saving}>{saving ? "저장 중…" : "가족 연결 저장"}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
