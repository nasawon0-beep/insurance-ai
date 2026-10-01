import { useMemo, useState } from "react";
import { rrnEnabled } from "./auth";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";

type Preview = {
  encoding: string;
  columns: string[];
  row_count: number;
  sample_rows: Record<string, string>[];
  auto_mapping?: Record<string, string>;
  import_analysis?: {
    header_row?: number | null;
    data_start_row?: number | null;
    registrable_count: number;
    needs_review_count: number;
    excluded_count: number;
    excluded_rows: { row: number; reason: string }[];
    review_rows: { row: number; reason: string }[];
    column_mapping: Record<string, any>;
  };
};
type Result = {
  encoding: string;
  created: number;
  merged: number;
  skipped: number;
  failed: { row: number; reason: string }[];
  warnings: string[];
  ignored_rrn: boolean;
  total_rows: number;
};

const FIELD_LABELS: Record<string, string> = {
  name: "이름 (필수)",
  phone: "전화",
  birth_date: "생년월일",
  gender: "성별 (M/F)",
  email: "이메일",
  address: "주소",
  occupation: "직업",
  tags: "태그",
  memo: "메모",
  customer_status: "상태 (가입/미가입/가망/해지)",
  rrn: "주민등록번호",
};

const GUESS: Record<string, RegExp> = {
  name: /이름|성명|성함|고객명|name/i,
  phone: /전화|휴대|핸드폰|연락처|H\.P|\bHP\b|phone|mobile|tel/i,
  birth_date: /생년월일|생일|birth|dob/i,
  gender: /성별|gender|sex/i,
  email: /이메일|메일|email/i,
  address: /주소|address|addr/i,
  occupation: /직업|occupation|job/i,
  tags: /태그|tag|분류/i,
  memo: /메모|비고|note|memo|remark/i,
  customer_status: /상태|status/i,
  rrn: /주민|rrn|resident/i,
};

const NONE = "";

export default function ImportWizard() {
  const fields = useMemo(
    () => Object.keys(FIELD_LABELS).filter((f) => f !== "rrn" || rrnEnabled()),
    [],
  );
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [dedupe, setDedupe] = useState<"merge" | "new" | "skip">("merge");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const loadFile = async (f: File) => {
    setFile(f);
    setResult(null);
    setErr(null);
    setPreview(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await engineFetch("/import/preview", { method: "POST", body: fd });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        throw new Error(formatErrorDetail(body?.detail) ?? `HTTP ${r.status}`);
      }
      const p: Preview = await r.json();
      setPreview(p);
      const auto: Record<string, string> = { ...(p.auto_mapping ?? {}) };
      for (const fld of fields) {
        if (auto[fld]) continue;
        const hit = p.columns.find((c) => GUESS[fld]?.test(c));
        if (hit) auto[fld] = hit;
      }
      setMapping(auto);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const pick = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    input.onchange = async () => {
      const f = input.files?.[0];
      if (!f) return;
      await loadFile(f);
    };
    input.click();
  };

  const commit = async () => {
    if (!file) return;
    if (!mapping.name) {
      setErr("이름 컬럼을 지정해야 합니다.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("mapping", JSON.stringify(mapping));
      fd.append("dedupe", dedupe);
      const r = await engineFetch("/import/commit", { method: "POST", body: fd });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        throw new Error(formatErrorDetail(body?.detail) ?? `HTTP ${r.status}`);
      }
      const res: Result = await r.json();
      setResult(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const sel: React.CSSProperties = { fontSize: 13, maxWidth: 200 };
  const btnPrimary: React.CSSProperties = {
    padding: "10px 24px", background: "var(--primary-600)", color: "#fff",
    border: "2px solid var(--primary-500)", borderRadius: 8, fontWeight: 700,
    fontSize: 14, cursor: "pointer", boxShadow: "0 2px 8px rgba(37,99,235,0.25)",
  };

  return (
    <div style={{ fontSize: 14 }}>
      {!preview && !result && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={async (e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) await loadFile(f);
          }}
          onClick={pick}
          style={{
            border: `2px dashed ${dragOver ? "var(--primary-500)" : "var(--color-border-hover)"}`,
            borderRadius: 12, padding: "32px 24px", textAlign: "center",
            background: dragOver ? "rgba(37,99,235,0.08)" : "var(--color-bg-surface)",
            marginBottom: 16, transition: "all 0.15s", cursor: "pointer",
          }}
        >
          <div style={{ fontSize: 32, marginBottom: 8 }}>📂</div>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4, color: "var(--color-text-primary)" }}>
            엑셀 파일을 여기에 끌어다 놓으세요
          </div>
          <div style={{ fontSize: 13, color: "var(--color-text-secondary)", marginBottom: 12 }}>
            .xlsx 또는 .csv 파일
          </div>
          <button onClick={(e) => { e.stopPropagation(); pick(); }} disabled={busy} style={btnPrimary}>
            {busy ? "읽는 중…" : "파일 선택"}
          </button>
        </div>
      )}

      {file && !result && (
        <div style={{ marginBottom: 8, fontSize: 13, color: "var(--color-text-secondary)" }}>📄 {file.name}</div>
      )}
      {err && <p style={{ color: "#f87171", fontSize: 13, marginBottom: 8 }}>{err}</p>}

      {preview && !result && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 13, color: "var(--color-text-secondary)", marginBottom: 8 }}>
            인코딩 {preview.encoding} · {preview.row_count}행 · 컬럼 {preview.columns.length}개
          </div>
          {preview.import_analysis && (
            <div style={{ padding: 12, border: "1px solid var(--color-border-default)", borderRadius: 8, background: "var(--color-bg-surface)", marginBottom: 12 }}>
              <b style={{ fontSize: 14 }}>검수 요약</b>
              <div style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 4 }}>
                헤더 {preview.import_analysis.header_row ?? "?"}행 · 데이터 시작 {preview.import_analysis.data_start_row ?? "?"}행 · 등록 가능{" "}
                <span style={{ color: "#4ade80", fontWeight: 700 }}>{preview.import_analysis.registrable_count}명</span> · 확인 필요 {preview.import_analysis.needs_review_count}행
              </div>
              {preview.import_analysis.review_rows.length > 0 && (
                <details style={{ marginTop: 4 }}>
                  <summary style={{ cursor: "pointer", color: "#fb923c" }}>확인 필요 행 {preview.import_analysis.review_rows.length}건</summary>
                  <ul style={{ margin: "4px 0", fontSize: 12 }}>
                    {preview.import_analysis.review_rows.slice(0, 10).map((r) => <li key={r.row}>{r.row}행: {r.reason}</li>)}
                  </ul>
                </details>
              )}
            </div>
          )}
          {preview.sample_rows.length > 0 && (
            <details style={{ marginBottom: 12 }} open>
              <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>샘플 미리보기 {preview.sample_rows.length}건</summary>
              <div style={{ overflowX: "auto", marginTop: 6 }}>
                <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr>{preview.columns.slice(0, 10).map((c) => <th key={c} style={{ border: "1px solid var(--color-border-default)", padding: "4px 8px", background: "var(--color-bg-hover)", textAlign: "left" }}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.slice(0, 5).map((row, i) => (
                      <tr key={i}>{preview.columns.slice(0, 10).map((c) => <td key={c} style={{ border: "1px solid var(--color-border-default)", padding: "4px 8px" }}>{String(row[c] ?? "")}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>컬럼 매핑</div>
            <table style={{ borderCollapse: "collapse" }}>
              <tbody>
                {Object.entries(FIELD_LABELS).filter(([f]) => fields.includes(f)).map(([f, label]) => (
                  <tr key={f}>
                    <td style={{ padding: "3px 12px 3px 0", color: "var(--color-text-secondary)", fontSize: 13, whiteSpace: "nowrap" }}>{label}</td>
                    <td>
                      <select style={sel} value={mapping[f] ?? NONE}
                        onChange={(e) => setMapping((m) => { const next = { ...m }; if (e.target.value === NONE) delete next[f]; else next[f] = e.target.value; return next; })}>
                        <option value={NONE}>(매핑 안 함)</option>
                        {preview.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
              중복 고객 처리{" "}
              <select value={dedupe} onChange={(e) => setDedupe(e.target.value as any)} style={sel}>
                <option value="merge">기존과 병합 (빈 칸만 채움)</option>
                <option value="new">항상 신규</option>
                <option value="skip">건너뜀</option>
              </select>
            </label>
          </div>
          <button onClick={commit} disabled={busy} style={{ ...btnPrimary, padding: "12px 32px", fontSize: 15 }}>
            {busy ? "가져오는 중…" : "가져오기 실행"}
          </button>
          <button onClick={pick} disabled={busy} style={{ marginLeft: 10, padding: "12px 20px", fontWeight: 600, fontSize: 14 }}>
            파일 다시 선택
          </button>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 10, fontSize: 14 }}>
          <p style={{ margin: "4px 0", color: "#4ade80", fontWeight: 700, fontSize: 15 }}>
            ✅ 완료 — 신규 {result.created} · 병합 {result.merged} · 건너뜀 {result.skipped} · 실패 {result.failed.length} / 전체 {result.total_rows}행
          </p>
          {result.warnings.length > 0 && (
            <details style={{ margin: "4px 0" }}>
              <summary style={{ color: "#fb923c", cursor: "pointer" }}>경고 {result.warnings.length}건</summary>
              <ul style={{ fontSize: 12, margin: "4px 0" }}>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}
          {result.failed.length > 0 && (
            <details style={{ margin: "4px 0" }} open>
              <summary style={{ color: "#f87171", cursor: "pointer" }}>실패 행 {result.failed.length}건</summary>
              <ul style={{ fontSize: 12, margin: "4px 0" }}>{result.failed.map((f, i) => <li key={i}>{f.row}행: {f.reason}</li>)}</ul>
            </details>
          )}
          <button onClick={() => { setResult(null); setPreview(null); setFile(null); }} style={{ marginTop: 12, padding: "10px 24px", fontWeight: 700, fontSize: 14 }}>
            새로 가져오기
          </button>
        </div>
      )}
    </div>
  );
}
