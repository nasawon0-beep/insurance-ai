import { Fragment, useCallback, useEffect, useState } from "react";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { engineFetch } from "./engine";
import { LEGACY_READONLY_NOTICE } from "./licenseStatus";

/**
 * 자료 보존 모드(중앙 회원 빌드 + 이 PC 기존 계정 로그인).
 * 기존 고객 자료 '조회'와 '내보내기'만 한다. 이 화면에서 쓰는 local-engine 호출은 GET 뿐이다
 * (/customers, /customers/{id}, /export, /exports) — 등록·수정·삭제·분석·AI 호출 경로가 없다.
 */
export const READONLY_ENGINE_PATHS = ["/customers", "/customers/{id}", "/export", "/exports"] as const;

type Customer = { id: string; name: string; phone?: string | null; birth_date?: string | null; customer_status?: string | null; effective_status?: string | null };
type Detail = Customer & {
  email?: string | null;
  address?: string | null;
  occupation?: string | null;
  memo?: string | null;
  tags?: string[] | null;
  policies?: { id: string; insurer?: string | null; product_name?: string | null; premium?: number | null; payment_cycle?: string | null; start_date?: string | null; end_date?: string | null; status?: string | null }[];
  consultations?: { id: string; consulted_at?: string | null; channel?: string | null; title?: string | null; content?: string | null }[];
};
type ExportItem = { filename: string; path: string; size?: number; created_at?: string };

async function getJson(path: string) {
  const r = await engineFetch(path, { method: "GET" });
  if (!r.ok) throw new Error(`자료를 불러오지 못했습니다 (HTTP ${r.status})`);
  return r.json();
}

async function reveal(path: string) {
  try {
    await revealItemInDir(path);
  } catch {
    alert("파일 위치:\n" + path);
  }
}

const box: React.CSSProperties = { border: "1px solid var(--color-border-default)", borderRadius: 8, padding: 12, background: "var(--color-bg-surface)" };

export default function LegacyReadonlyView({ email, onLogout }: { email: string; onLogout: () => void }) {
  const [q, setQ] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [exports, setExports] = useState<ExportItem[]>([]);
  const [last, setLast] = useState<{ filename?: string; path: string; rows: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadCustomers = useCallback(async (query: string) => {
    setErr(null);
    try {
      const qs = new URLSearchParams({ sort: "name", limit: "5000" });
      if (query.trim()) qs.set("q", query.trim());
      const b = await getJson(`/customers?${qs}`);
      setCustomers(b.customers || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  const loadExports = useCallback(async () => {
    try {
      setExports((await getJson("/exports")).exports || []);
    } catch {
      /* 목록 실패는 무시 */
    }
  }, []);

  useEffect(() => {
    void loadCustomers("");
    void loadExports();
  }, [loadCustomers, loadExports]);

  const open = async (id: string) => {
    setErr(null);
    try {
      setDetail(await getJson(`/customers/${encodeURIComponent(id)}`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const makeExport = async () => {
    setBusy(true);
    setErr(null);
    try {
      setLast(await getJson("/export"));
      void loadExports();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 16 }} data-testid="legacy-readonly">
      <div role="status" style={{ background: "#fff7e6", color: "#7a4b00", fontSize: 13, padding: "10px 12px", border: "1px solid #f0d9a8", borderRadius: 8, marginBottom: 12 }}>
        {LEGACY_READONLY_NOTICE}
        <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: "#555" }}>이 PC 계정: {email}</span>
          <button type="button" onClick={onLogout}>모아둠 계정으로 로그인 / 로그아웃</button>
        </div>
      </div>
      {err && <p role="alert" style={{ color: "#b00", fontSize: 13 }}>{err}</p>}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 1fr) 2fr", gap: 12 }}>
        <div style={box}>
          <form onSubmit={(e) => { e.preventDefault(); void loadCustomers(q); }} style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="이름·전화 검색" style={{ flex: 1 }} aria-label="고객 검색" />
            <button type="submit">검색</button>
          </form>
          <div style={{ fontSize: 12, color: "#666", marginBottom: 4 }}>고객 {customers.length}명</div>
          <div style={{ maxHeight: 520, overflowY: "auto" }}>
            {customers.map((c) => (
              <button key={c.id} type="button" onClick={() => void open(c.id)}
                style={{ display: "block", width: "100%", textAlign: "left", padding: "6px 8px", border: "none", borderBottom: "1px solid var(--color-border-default)", background: detail?.id === c.id ? "var(--neutral-50)" : "transparent", cursor: "pointer" }}>
                <b>{c.name}</b> <span style={{ color: "#888", fontSize: 12 }}>{c.phone || ""} {c.effective_status || c.customer_status || ""}</span>
              </button>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={box}>
            {!detail ? (
              <p style={{ color: "#888" }}>왼쪽에서 고객을 선택하면 저장된 정보를 볼 수 있습니다(보기 전용).</p>
            ) : (
              <div>
                <h3 style={{ margin: "0 0 8px" }}>{detail.name}</h3>
                <dl style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: "4px 8px", fontSize: 13, margin: 0 }}>
                  {([["연락처", detail.phone], ["생년월일", detail.birth_date], ["이메일", detail.email], ["주소", detail.address], ["직업", detail.occupation],
                    ["상태", detail.effective_status || detail.customer_status], ["태그", (detail.tags || []).join(", ")], ["메모", detail.memo]] as [string, unknown][])
                    .filter(([, v]) => v).map(([k, v]) => (<Fragment key={k}><dt style={{ color: "#666" }}>{k}</dt><dd style={{ margin: 0, whiteSpace: "pre-wrap" }}>{String(v)}</dd></Fragment>))}
                </dl>
                <h4 style={{ margin: "12px 0 4px" }}>계약 {detail.policies?.length ?? 0}건</h4>
                {(detail.policies || []).map((p) => (
                  <div key={p.id} style={{ fontSize: 13, padding: "3px 0", borderBottom: "1px dashed var(--color-border-default)" }}>
                    {p.insurer || "-"} · {p.product_name || "-"} {p.premium != null ? `· ${p.premium.toLocaleString()}원${p.payment_cycle ? "/" + p.payment_cycle : ""}` : ""} {p.start_date ? `· ${p.start_date}~${p.end_date || ""}` : ""} {p.status ? `· ${p.status}` : ""}
                  </div>
                ))}
                <h4 style={{ margin: "12px 0 4px" }}>상담 {detail.consultations?.length ?? 0}건</h4>
                {(detail.consultations || []).map((c) => (
                  <div key={c.id} style={{ fontSize: 13, padding: "3px 0", borderBottom: "1px dashed var(--color-border-default)", whiteSpace: "pre-wrap" }}>
                    <span style={{ color: "#666" }}>{c.consulted_at || ""} {c.channel || ""}</span> {c.title ? `[${c.title}] ` : ""}{c.content || ""}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={box}>
            <b>데이터 내보내기</b>
            <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>고객·계약·상담을 CSV(엑셀에서 열림) zip으로 이 PC에 저장합니다. 주민등록번호는 포함하지 않습니다.</p>
            <button type="button" onClick={() => void makeExport()} disabled={busy}>{busy ? "만드는 중…" : "내보내기 파일 만들기"}</button>
            {last && (
              <p style={{ fontSize: 12, color: "#161", margin: "6px 0" }}>
                {last.filename || last.path} — {last.rows}행 <button type="button" style={{ fontSize: 11 }} onClick={() => void reveal(last.path)}>폴더 열기</button>
              </p>
            )}
            <div style={{ marginTop: 6, maxHeight: 120, overflowY: "auto" }}>
              {exports.map((it) => (
                <div key={it.filename} style={{ display: "flex", gap: 8, fontSize: 12, padding: "2px 0" }}>
                  <span style={{ flex: 1 }}>{it.filename} <span style={{ color: "#999" }}>{it.created_at || ""}</span></span>
                  <button type="button" style={{ fontSize: 11 }} onClick={() => void reveal(it.path)}>폴더 열기</button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
