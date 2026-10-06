import { useState } from "react";
import { groupByCategory } from "./coverageCategories";
import { normalizeCoverageRows, parseCoverageJson, type CoverageRow } from "./coverageRows";

export { normalizeCoverageRows } from "./coverageRows";
export type { CoverageRow } from "./coverageRows";

// 보험 문서(가입제안서 1건 / 보장분석 N건)에서 뽑은 계약을 검토·저장하는 공용 UI + 저장 헬퍼.
export type PolicyCoverageDraft = {
  policy_id?: string | null;
  source_document_id?: string | null;
  source_file_name?: string | null;
  source_hash?: string | null;
  source_page?: number | null;
  insurer?: string | null;
  product_name?: string | null;
  rider_no?: string | null;
  coverage_type?: string | null;
  rider_name?: string | null;
  standard_name?: string | null;
  amount?: number | null;
  amount_text?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  confidence?: number | null;
  raw_text?: string | null;
};
export type PolicyDraft = Record<string, string | number | boolean | null | PolicyCoverageDraft[] | undefined>;

// 계약자(피보험자)와의 관계 드롭다운. "" = 본인계약(계약자 = 피보험자).
export const POLICYHOLDER_RELS = [
  "",
  "본인",
  "배우자",
  "부",
  "모",
  "자녀",
  "형제자매",
  "사업자",
  "기타",
] as const;

// 보험계약 상태: 저장·전송값은 영어 코드(ACTIVE/LAPSED/EXPIRED/CANCELLED) 그대로, 화면 표시만 한글.
export const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "유지중",
  LAPSED: "실효(납입중단)",
  EXPIRED: "만기종료",
  CANCELLED: "해지",
};

function Field({
  label,
  value,
  width,
  onChange,
}: {
  label: string;
  value: unknown;
  width: number;
  onChange: (v: string | null) => void;
}) {
  return (
    <label style={{ fontSize: 10, color: "#555" }}>
      {label}
      <input
        style={{ display: "block", padding: 3, width, fontSize: 12 }}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      />
    </label>
  );
}

export function PolicyList({
  policies,
  checked,
  docType,
  onToggle,
  onEdit,
}: {
  policies: PolicyDraft[];
  checked: boolean[];
  docType?: string;
  onToggle: (i: number, v: boolean) => void;
  onEdit: (i: number, patch: PolicyDraft) => void;
}) {
  if (!policies.length) return null;
  const label = docType === "보장분석" ? "보유 보험계약" : "보험계약";
  return (
    <div style={{ margin: "8px 0", padding: "8px 10px", background: "#eef4ff", borderRadius: 6, maxWidth: 640 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>
        📋 {label} {policies.length}건 — 체크한 계약을 고객 가입목록에 추가
      </div>
      <div style={{ fontSize: 11, color: "#888", margin: "2px 0 6px" }}>
        보험사나 상품명 중 하나는 있어야 저장됩니다.
      </div>
      {policies.map((p, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: 6,
            alignItems: "flex-start",
            padding: "5px 0",
            borderTop: i ? "1px solid #dbe4f5" : undefined,
          }}
        >
          <input
            type="checkbox"
            checked={checked[i] ?? true}
            onChange={(e) => onToggle(i, e.target.checked)}
            style={{ marginTop: 4 }}
          />
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", flex: 1, alignItems: "flex-end" }}>
            <Field label="보험사" value={p.insurer} width={100} onChange={(x) => onEdit(i, { insurer: x })} />
            <Field label="상품명" value={p.product_name} width={240} onChange={(x) => onEdit(i, { product_name: x })} />
            <Field label="월보험료(원)" value={p.premium_won} width={90} onChange={(x) => onEdit(i, { premium_won: x })} />
            <Field label="보험기간" value={p.insured_period} width={80} onChange={(x) => onEdit(i, { insured_period: x })} />
            <Field label="납입" value={p.payment_period} width={90} onChange={(x) => onEdit(i, { payment_period: x })} />
            <Field
              label="계약자 (피보험자와 다르면)"
              value={p.policyholder_name}
              width={120}
              onChange={(x) => onEdit(i, { policyholder_name: x })}
            />
            <label style={{ fontSize: 10, color: "#555" }}>
              관계
              <select
                style={{ display: "block", padding: 3, fontSize: 12 }}
                value={(p.policyholder_rel as string) ?? ""}
                onChange={(e) => onEdit(i, { policyholder_rel: e.target.value || null })}
              >
                {POLICYHOLDER_RELS.map((r) => (
                  <option key={r} value={r}>
                    {r || "—"}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ fontSize: 10, color: "#555", display: "flex", alignItems: "center", gap: 3, paddingBottom: 4 }}>
              <input
                type="checkbox"
                checked={p.is_own !== false}
                onChange={(e) => onEdit(i, { is_own: e.target.checked })}
              />
              내 계약
            </label>
            {Array.isArray(p.policy_coverages) && p.policy_coverages.length > 0 && (
              <div style={{ flexBasis: "100%", fontSize: 11, color: "#475569", background: "#fff", border: "1px solid #dbe4f5", borderRadius: 4, padding: "4px 6px" }}>
                담보 원장 {p.policy_coverages.length}건 검출 · 예: {p.policy_coverages.slice(0, 3).map((c) => `${c.rider_name ?? "담보"} ${c.amount_text ?? ""}`).join(" / ")}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  미가입: { bg: "#eef0f1", fg: "#6b767e" },
  부족: { bg: "#fbe8e6", fg: "#c93327" },
  충분: { bg: "#e7ecfb", fg: "#2352cc" },
};

function parseCoverageAmount(value: string | null): number {
  const amount = Number(String(value ?? "").replace(/[^\d]/g, ""));
  return Number.isFinite(amount) ? amount : 0;
}

function recalculateCoveragePct(row: CoverageRow): CoverageRow {
  const current = parseCoverageAmount(row.current);
  const recommended = parseCoverageAmount(row.recommended);
  const pct = recommended > 0 ? Math.round((current / recommended) * 100) : 0;
  return { ...row, pct };
}

/** 보장분석서의 '보장현황'을 표로. 미가입 → 부족 → 충분 순으로 정렬해서 보여준다. */
export function CoverageTable({ rows }: { rows: CoverageRow[] }) {
  if (!rows.length) return null;
  const order: Record<string, number> = { 미가입: 0, 부족: 1, 충분: 2 };
  const sorted = [...rows].sort(
    (a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || a.pct - b.pct,
  );
  const groups = groupByCategory(sorted);
  const gap = sorted.filter((r) => r.status !== "충분").length;
  return (
    <div style={{ margin: "8px 0", maxWidth: 640 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
        📊 보장현황 <span style={{ fontWeight: 400, color: "#888" }}>({rows.length}개 항목 · 보완 필요 {gap})</span>
      </div>
      {groups.map((group) => (
        <div key={group.title} style={{ marginTop: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>
            {group.title} ({group.rows.length}개)
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
          <thead>
            <tr style={{ background: "var(--color-bg-hover)", textAlign: "left" }}>
              <th style={{ padding: "4px 8px" }}>항목</th>
              <th style={{ padding: "4px 8px" }}>상태</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>충족률</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>현재 보장</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>권장</th>
            </tr>
          </thead>
          <tbody>
            {group.rows.map((r, i) => {
              const s = STATUS_STYLE[r.status] ?? { bg: "#eee", fg: "#555" };
              return (
                <tr key={i} style={{ borderTop: "1px solid var(--color-border-default)" }}>
                  <td style={{ padding: "4px 8px" }}>{r.name}</td>
                  <td style={{ padding: "4px 8px" }}>
                    <span style={{ background: s.bg, color: s.fg, borderRadius: 4, padding: "1px 6px", fontWeight: 600 }}>
                      {r.status}
                    </span>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "right", color: s.fg }}>{r.pct}%</td>
                  <td style={{ padding: "4px 8px", textAlign: "right" }}>{r.current ?? "-"}</td>
                  <td style={{ padding: "4px 8px", textAlign: "right", color: "var(--color-text-secondary)" }}>{r.recommended ?? "-"}</td>
                </tr>
              );
            })}
          </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

export function CoverageTableEditable({
  rows,
  onCommit,
}: {
  rows: CoverageRow[];
  onCommit: (rows: CoverageRow[]) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CoverageRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (!rows.length) return null;

  const startEditing = () => {
    setDraft(rows.map((row) => ({ ...row })));
    setErr(null);
    setEditing(true);
  };
  const update = (index: number, patch: Partial<CoverageRow>) => {
    setDraft((current) => current.map((row, i) => {
      if (i !== index) return row;
      const next = { ...row, ...patch };
      return patch.current !== undefined || patch.recommended !== undefined ? recalculateCoveragePct(next) : next;
    }));
  };
  const save = async () => {
    setErr(null);
    setSaving(true);
    try {
      const normalized = normalizeCoverageRows(draft);
      await onCommit(normalized);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div style={{ margin: "8px 0", maxWidth: 640 }}>
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center" }}>
          <button style={{ fontSize: 12, color: "#2563eb" }} onClick={startEditing}>편집</button>
        </div>
        <CoverageTable rows={rows} />
      </div>
    );
  }

  return (
    <div style={{ margin: "8px 0", maxWidth: 640 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>📊 보장현황</div>
        <div style={{ display: "flex", gap: 4 }}>
          <button style={{ fontSize: 12, color: "#2563eb" }} onClick={save} disabled={saving}>저장</button>
          <button style={{ fontSize: 12 }} onClick={() => { setEditing(false); setErr(null); }}>취소</button>
        </div>
      </div>
      {err && <div style={{ color: "#b00", fontSize: 12, marginBottom: 4 }}>{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
          <thead>
            <tr style={{ background: "var(--color-bg-hover)", textAlign: "left" }}>
              <th style={{ padding: "4px 8px" }}>항목</th>
              <th style={{ padding: "4px 8px" }}>상태</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>충족률</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>현재 보장</th>
              <th style={{ padding: "4px 8px", textAlign: "right" }}>권장</th>
              <th style={{ padding: "4px 8px" }} />
            </tr>
          </thead>
          <tbody>
            {draft.map((row, i) => {
              const statuses = ["미가입", "부족", "충분"];
              if (!statuses.includes(row.status)) statuses.push(row.status);
              const input = { padding: 3, fontSize: 12, width: "100%", boxSizing: "border-box" as const };
              return (
                <tr key={i} style={{ borderTop: "1px solid #e3e8f0" }}>
                  <td style={{ padding: "4px 8px" }}><input type="text" style={input} value={row.name} onChange={(e) => update(i, { name: e.target.value })} /></td>
                  <td style={{ padding: "4px 8px" }}>
                    <select style={{ ...input, width: "auto" }} value={row.status} onChange={(e) => update(i, { status: e.target.value })}>
                      {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
                    </select>
                  </td>
                  <td style={{ padding: "4px 8px", textAlign: "right" }}>{row.pct}%</td>
                  <td style={{ padding: "4px 8px" }}><input type="text" style={input} value={row.current ?? ""} onChange={(e) => update(i, { current: e.target.value })} /></td>
                  <td style={{ padding: "4px 8px" }}><input type="text" style={input} value={row.recommended ?? ""} onChange={(e) => update(i, { recommended: e.target.value })} /></td>
                  <td style={{ padding: "4px 8px" }}><button style={{ fontSize: 12, color: "#b00" }} onClick={() => setDraft((current) => current.filter((_, j) => j !== i))}>삭제</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button
        style={{ marginTop: 6, fontSize: 12, color: "#2563eb" }}
        onClick={() => setDraft((current) => [...current, { name: "", status: "미가입", pct: 0, current: null, recommended: null }])}
      >
        + 담보 행 추가
      </button>
    </div>
  );
}

function CoverageCorruptNotice() {
  return (
    <div style={{ color: "#b00", fontSize: 12, margin: "6px 0" }}>
      저장된 보장분석 데이터를 읽을 수 없습니다 (형식 손상). 분석을 다시 실행하거나 표를 새로 만들어 주세요.
    </div>
  );
}

/** 저장된 consultation.coverage_json(문자열) → 표. 손상 시 그 사실을 표시한다. */
export function CoverageTableSafe({ json }: { json: string | null }) {
  const { rows, corrupt } = parseCoverageJson(json);
  if (corrupt) return <CoverageCorruptNotice />;
  return Array.isArray(rows) && rows.length ? <CoverageTable rows={rows} /> : null;
}

export function CoverageTableEditableSafe({
  json,
  onCommit,
}: {
  json: string | null;
  onCommit: (rows: CoverageRow[]) => void | Promise<void>;
}) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { rows, corrupt } = parseCoverageJson(json);

  if (corrupt) return <CoverageCorruptNotice />;
  if (!json) return null;

  if (!rows.length) {
    const create = async () => {
      setErr(null);
      setBusy(true);
      try {
        await onCommit([{ name: "", status: "미가입", pct: 0, current: null, recommended: null }]);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    };
    return (
      <div style={{ margin: "6px 0" }}>
        <button style={{ fontSize: 12, color: "#2563eb" }} onClick={create} disabled={busy}>
          + 보장현황 표 만들기
        </button>
        {err && <div style={{ color: "#b00", fontSize: 12, marginTop: 4 }}>{err}</div>}
      </div>
    );
  }
  return <CoverageTableEditable rows={rows} onCommit={onCommit} />;
}

/** 체크된 계약들을 고객 가입목록(POST /customers/{cid}/policies)에 등록. 실패해도 나머지는 진행. */
export async function savePolicies(
  api: (path: string, init: RequestInit) => Promise<unknown>,
  cid: string,
  policies: PolicyDraft[],
  checked: boolean[],
): Promise<{ saved: number; err?: string; failures: Array<{ index: number; label: string; error: string }>; coverageSaved: number; coverageLinkFailed: number }> {
  let saved = 0;
  let coverageSaved = 0;
  let coverageLinkFailed = 0;
  let err: string | undefined;
  const failures: Array<{ index: number; label: string; error: string }> = [];
  for (let i = 0; i < policies.length; i++) {
    if (checked[i] === false) continue;
    const p = policies[i];
    const label = (`${p.insurer ?? ""} ${p.product_name ?? ""}`).trim() || `${i + 1}번째 계약`;
    if (!p.insurer && !p.product_name && !p.premium_won) continue;
    const prem = Number(String(p.premium_won ?? "").replace(/[^\d]/g, ""));
    const memo =
      [
        p.insured_period && `보험기간 ${p.insured_period}`,
        p.payment_period && `납입기간 ${p.payment_period}`,
      ]
        .filter(Boolean)
        .join(" · ") || null;
    try {
      const created = await api(`/customers/${cid}/policies`, {
        method: "POST",
        body: JSON.stringify({
          insurer: p.insurer || null,
          product_name: p.product_name || null,
          plan_type: (p.plan_type as string) || null,
          premium: prem > 0 ? prem : null,
          payment_cycle: (p.payment_cycle as string) || "MONTHLY",
          start_date: (p.issued_date as string) || null,
          // 보험기간/납입기간은 정식 필드로도 보낸다 → 서버가 만기·납입종료일 자동 산출.
          // memo 크램(위 `memo`)은 하위호환용으로 유지.
          insured_period: (p.insured_period as string) || null,
          payment_period: (p.payment_period as string) || null,
          is_own: p.is_own !== false, // 체크 해제(명시적 false)만 아니면 내 계약
          // 계약자(피보험자와 다를 때). 비어있으면 본인계약.
          policyholder_name: (p.policyholder_name as string) || null,
          policyholder_rel: (p.policyholder_rel as string) || null,
          status: "ACTIVE",
          memo,
        }),
      });
      saved++;
      const policyId = typeof created === "object" && created && "id" in created ? String((created as { id: string }).id) : null;
      const coverages = Array.isArray(p.policy_coverages) ? p.policy_coverages : [];
      if (policyId && coverages.length > 0) {
        const coverageResult = await api(`/customers/${cid}/policy-coverages/bulk`, {
          method: "POST",
          body: JSON.stringify({
            review_confirmed: true,
            items: coverages.map((c) => ({ ...c, policy_id: policyId })),
          }),
        }) as { created?: number; policy_link_failed?: number };
        coverageSaved += Number(coverageResult.created ?? 0);
        coverageLinkFailed += Number(coverageResult.policy_link_failed ?? 0);
      }
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
      failures.push({ index: i, label, error: e instanceof Error ? e.message : String(e) });
    }
  }
  return { saved, err, failures, coverageSaved, coverageLinkFailed };
}
