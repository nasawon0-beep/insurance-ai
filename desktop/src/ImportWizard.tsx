import { useMemo, useState } from "react";
import { rrnEnabled } from "./auth";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";

type Preview = {
  encoding: string;
  columns: string[];
  row_count: number;
  sample_rows: Record<string, string>[];
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
  name: /이름|성명|고객명|name/i,
  phone: /전화|휴대|핸드폰|연락처|phone|mobile|tel/i,
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

  const pick = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    input.onchange = async () => {
      const f = input.files?.[0];
      if (!f) return;
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
        // 헤더 이름으로 자동 매핑 추정
        const auto: Record<string, string> = {};
        for (const fld of fields) {
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
      // usage 기록은 엔진(POST /import/commit)이 정본. 프론트 중복 기록 안 함.
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const sel: React.CSSProperties = { fontSize: 12, maxWidth: 180 };

  return (
    <div style={{ fontSize: 13 }}>
      <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>
        엑셀(.xlsx) 또는 CSV 파일에서 고객을 가져옵니다 (v1: 고객만)
      </p>
      <button onClick={pick} disabled={busy}>
        {busy && !preview ? "읽는 중…" : "파일 선택"}
      </button>
      {file && <span style={{ marginLeft: 8, color: "#555" }}>{file.name}</span>}
      {err && <p style={{ color: "#b00", fontSize: 12 }}>{err}</p>}

      {preview && !result && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, color: "#666" }}>
            인코딩 {preview.encoding} · {preview.row_count}행 · 컬럼 {preview.columns.length}개
          </div>
          <table style={{ borderCollapse: "collapse", marginTop: 8 }}>
            <tbody>
              {Object.entries(FIELD_LABELS)
                .filter(([f]) => fields.includes(f))
                .map(([f, label]) => (
                  <tr key={f}>
                    <td style={{ padding: "2px 8px 2px 0", color: "#555" }}>{label}</td>
                    <td>
                      <select
                        style={sel}
                        value={mapping[f] ?? NONE}
                        onChange={(e) =>
                          setMapping((m) => {
                            const next = { ...m };
                            if (e.target.value === NONE) delete next[f];
                            else next[f] = e.target.value;
                            return next;
                          })
                        }
                      >
                        <option value={NONE}>(매핑 안 함)</option>
                        {preview.columns.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          <div style={{ marginTop: 8 }}>
            <label style={{ fontSize: 12, color: "#555" }}>
              중복 고객 처리{" "}
              <select value={dedupe} onChange={(e) => setDedupe(e.target.value as any)} style={sel}>
                <option value="merge">기존과 병합 (빈 칸만 채움)</option>
                <option value="new">항상 신규</option>
                <option value="skip">건너뜀</option>
              </select>
            </label>
          </div>

          <button onClick={commit} disabled={busy} style={{ marginTop: 10, fontWeight: 600 }}>
            {busy ? "가져오는 중…" : "가져오기 실행"}
          </button>
        </div>
      )}

      {result && (
        <div style={{ marginTop: 10, fontSize: 13 }}>
          <p style={{ margin: "4px 0", color: "#161" }}>
            완료 — 신규 {result.created} · 병합 {result.merged} · 건너뜀 {result.skipped} · 실패{" "}
            {result.failed.length} / 전체 {result.total_rows}행
          </p>
          {result.warnings.length > 0 && (
            <details style={{ margin: "4px 0" }}>
              <summary style={{ color: "#8a4b00", cursor: "pointer" }}>경고 {result.warnings.length}건</summary>
              <ul style={{ fontSize: 12, margin: "4px 0" }}>
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}
          {result.failed.length > 0 && (
            <details style={{ margin: "4px 0" }} open>
              <summary style={{ color: "#b00", cursor: "pointer" }}>실패 행 {result.failed.length}건</summary>
              <ul style={{ fontSize: 12, margin: "4px 0" }}>
                {result.failed.map((f, i) => (
                  <li key={i}>
                    {f.row}행: {f.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <button onClick={() => { setResult(null); setPreview(null); setFile(null); }} style={{ marginTop: 6 }}>
            새로 가져오기
          </button>
        </div>
      )}
    </div>
  );
}
