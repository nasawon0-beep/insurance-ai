import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import CustomersScreen from "./Customers";
import AssistantScreen from "./Assistant";
import { CoverageTable, PolicyList, savePolicies, type PolicyDraft } from "./PolicyList";
import {
  authenticate,
  changePassword,
  fetchMe,
  getToken,
  getUser,
  listDevices,
  listLocalAccounts,
  loadEngineSettings,
  localRecoveryStatus,
  logout,
  removeDevice,
  resetLocalPassword,
  resolveLicense,
  rrnEnabled,
} from "./auth";
import { track } from "./usage";
import ImportWizard from "./ImportWizard";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { engineFetch } from "./engine";
import { checkForUpdate } from "./updater";
import { loadRememberedEmail, saveRememberedEmail } from "./rememberEmail";

type EngineStatus = {
  local_engine?: string;
  ollama?: "connected" | "disconnected";
  models_available?: string[];
  error?: string;
};

type SttStatus = {
  state: string;
  pct: number | null;
  mb: number;
  total_mb: number;
  reason: string | null;
  backend: "cli" | "faster";
};

const AUDIO_RE = /\.(m4a|mp3|wav|aac|aiff?|ogg|oga|flac|opus|wma|amr|3gp|mp4|mov|m4b|webm)$/i;

function audioDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = new Audio(url);
    let settled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const done = (value: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      URL.revokeObjectURL(url);
      resolve(value);
    };
    timeout = setTimeout(() => done(null), 3000);
    audio.onloadedmetadata = () => done(Number.isFinite(audio.duration) ? audio.duration : null);
    audio.onerror = () => done(null);
  });
}

function sttPhrase(backend: SttStatus["backend"], durationSec: number | null): string {
  if (!durationSec) return "🎙 녹취 파일 전사·분석 중… 창을 닫지 마세요.";
  const minutes = Math.max(1, Math.ceil(durationSec / 60));
  const [lowRate, highRate] = backend === "cli" ? [0.1, 0.3] : [0.8, 1.5];
  const low = Math.max(1, Math.ceil(minutes * lowRate + 0.5));
  const high = Math.max(low, Math.ceil(minutes * highRate + 0.5));
  return `🎙 ${minutes}분짜리 통화입니다. 전사에 ${low}~${high}분쯤 걸립니다. 창을 닫지 마세요.`;
}

type FollowUp = {
  id: string;
  customer_id: string;
  customer_name: string | null;
  title: string | null;
  follow_up_at: string | null;
};

type ExpiringPolicy = {
  id: string;
  customer_id: string;
  customer_name: string | null;
  insurer: string | null;
  product_name: string | null;
  end_date: string | null;
  end_date_derived?: number | null;
  insured_period?: string | null;
  days?: number | null;
};

type Birthday = {
  id: string;
  name: string;
  birth_date: string | null;
  days_until: number;
  turning_age: number;
  estimated: boolean;
};

type RecentConsult = {
  id: string;
  customer_id: string;
  customer_name: string | null;
  title: string | null;
  channel: string | null;
  consulted_at: string;
};

type Dashboard = {
  counts: { customers: number; policies: number; active_policies: number; consultations: number };
  follow_ups: FollowUp[];
  expiring_policies: ExpiringPolicy[];
  birthdays: Birthday[];
  recent_consultations: RecentConsult[];
};

function dday(dateStr: string | null): { label: string; color: string } | null {
  if (!dateStr) return null;
  const d = new Date(dateStr.slice(0, 10) + "T00:00:00");
  if (isNaN(d.getTime())) return null;
  const days = Math.ceil((d.getTime() - Date.now()) / 86400000);
  if (days < 0) return { label: `${-days}일 지남`, color: "#b00" };
  if (days === 0) return { label: "오늘", color: "#c60" };
  return { label: `D-${days}`, color: days <= 7 ? "#c60" : "#888" };
}

type ParsedPage = {
  page: number;
  text: string;
  char_count: number;
  tables: unknown[][];
  empty: boolean;
};

type ParsedDoc = {
  doc_id: string;
  filename: string;
  page_count: number;
  metadata: Record<string, string>;
  pages: ParsedPage[];
  extracted_at: string;
};

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const rememberedEmail = loadRememberedEmail();
  const [email, setEmail] = useState(rememberedEmail);
  const [rememberEmail, setRememberEmail] = useState(rememberedEmail !== "");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<"closed" | "loading" | "available" | "unavailable">("closed");
  const [accounts, setAccounts] = useState<{ email: string; created_at?: string }[]>([]);
  const [recoveryEmail, setRecoveryEmail] = useState("");
  const [recoveryPw, setRecoveryPw] = useState("");
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoveryErr, setRecoveryErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const openRecovery = async () => {
    setRecovery("loading");
    setRecoveryErr(null);
    setMsg(null);
    if (!(await localRecoveryStatus())) {
      setRecovery("unavailable");
      return;
    }
    try {
      const found = await listLocalAccounts();
      setAccounts(found);
      setRecoveryEmail(found[0]?.email ?? "");
      setRecovery("available");
    } catch {
      setRecovery("unavailable");
    }
  };

  const submitRecovery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!recoveryEmail || recoveryPw.length < 6) {
      setRecoveryErr("계정과 6자 이상 새 비밀번호를 입력하세요.");
      return;
    }
    setRecoveryBusy(true);
    setRecoveryErr(null);
    try {
      await resetLocalPassword(recoveryEmail, recoveryPw);
      setEmail(recoveryEmail);
      setPw("");
      setRecoveryPw("");
      setRecovery("closed");
      setMode("login");
      setErr(null);
      setMsg("새 비밀번호를 설정했습니다. 로그인해 주세요.");
    } catch (e) {
      setRecoveryErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRecoveryBusy(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || pw.length < 6) {
      setErr("이메일과 6자 이상 비밀번호를 입력하세요.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const normalizedEmail = email.trim().toLowerCase();
      await authenticate(mode, normalizedEmail, pw);
      saveRememberedEmail(normalizedEmail, rememberEmail);
      onLogin();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 320, margin: "80px auto" }}>
      <h2>Insurance AI</h2>
      <div style={{ display: "flex", gap: 6, fontSize: 13 }}>
        <button
          onClick={() => setMode("login")}
          style={{ fontWeight: mode === "login" ? 700 : 400 }}
        >
          로그인
        </button>
        <button
          onClick={() => setMode("register")}
          style={{ fontWeight: mode === "register" ? 700 : 400 }}
        >
          회원가입
        </button>
      </div>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input
          placeholder="비밀번호 (6자 이상)"
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={rememberEmail}
            onChange={(e) => setRememberEmail(e.target.checked)}
          />
          이메일 기억하기
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "..." : mode === "login" ? "로그인" : "가입하고 시작"}
        </button>
      </form>
      {err && <small style={{ color: "#b00" }}>{err}</small>}
      {msg && <small style={{ color: "#176b2c" }}>{msg}</small>}
      {recovery === "closed" ? (
        <button
          type="button"
          onClick={openRecovery}
          style={{ border: 0, background: "none", padding: 0, color: "#3567a8", textAlign: "left", textDecoration: "underline" }}
        >
          비밀번호나 가입 이메일을 잊으셨나요?
        </button>
      ) : (
        <div style={{ border: "1px solid #ddd", borderRadius: 6, padding: 10, fontSize: 13 }}>
          {recovery === "loading" && <div>계정 복구 정보를 확인하고 있습니다...</div>}
          {recovery === "available" && (
            <form onSubmit={submitRecovery} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <strong>이 컴퓨터에 저장된 계정</strong>
              {accounts.map((account) => (
                <label key={account.email}>
                  <input
                    type="radio"
                    name="recovery-account"
                    checked={recoveryEmail === account.email}
                    onChange={() => setRecoveryEmail(account.email)}
                  />{" "}
                  {account.email}{account.created_at ? ` (${account.created_at.slice(0, 10)})` : ""}
                </label>
              ))}
              {accounts.length === 0 && <span>저장된 계정이 없습니다.</span>}
              {accounts.length > 0 && (
                <>
                  <input
                    type="password"
                    placeholder="새 비밀번호 (6자 이상)"
                    value={recoveryPw}
                    onChange={(e) => setRecoveryPw(e.target.value)}
                  />
                  <button type="submit" disabled={recoveryBusy || !recoveryEmail}>
                    {recoveryBusy ? "..." : "비밀번호 재설정"}
                  </button>
                </>
              )}
              {recoveryErr && <small style={{ color: "#b00" }}>{recoveryErr}</small>}
              <small>이 목록은 설계사님 컴퓨터 안의 인증 서버에만 있습니다. 인터넷으로 나가지 않습니다.</small>
            </form>
          )}
          {recovery === "unavailable" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span>이 컴퓨터에서는 앱에서 바로 재설정할 수 없습니다. 서버를 운영하는 PC의 control-server 폴더에서 아래를 실행하세요.</span>
              <code>macOS: cd control-server &amp;&amp; .venv/bin/python reset_password.py --list</code>
              <code>Windows: cd control-server &amp;&amp; .venv\Scripts\python.exe reset_password.py --list</code>
            </div>
          )}
        </div>
      )}
      <div style={{ fontSize: 14, fontWeight: 700 }}>
        <div>🔒 고객 정보는 100% 설계사님 본인 컴퓨터 안에만 있습니다.</div>
        <div>클라우드로 전송되지 않으며, 인터넷이 끊겨도 고객 관리는 그대로 작동합니다.</div>
      </div>
      <small style={{ color: "#888" }}>
        고객 이름·연락처·주민등록번호·병력 메모·상담 녹취 — 모두 이 PC에 암호화되어 저장됩니다.
        로그인할 때만 인증 서버에 연결하며, 이때 오가는 정보는 이메일과 비밀번호뿐입니다 (고객 데이터
        미포함).
      </small>
    </div>
  );
}

function EngineStrip() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  useEffect(() => {
    const poll = async () => {
      try {
        setStatus(await (await engineFetch("/health")).json());
      } catch {
        setStatus({ local_engine: "unreachable" });
      }
    };
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);
  const ok = status?.local_engine === "ok";
  const ollamaOk = status?.ollama === "connected";
  if (status === null || (ok && ollamaOk)) return null;

  return (
    <div style={{ fontSize: 12, color: "#8a6500", background: "#fff8e1", padding: 8, marginBottom: 12 }}>
      {ok
        ? "⚠ AI 분석 기능이 지금 안 됩니다. (고객 관리는 정상 작동합니다.)"
        : "⚠ 일부 기능이 일시적으로 멈췄습니다. 앱을 껐다 다시 켜 주세요."}
    </div>
  );
}

function Card({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12, marginBottom: 14 }}>
      <h3 style={{ margin: "0 0 8px" }}>
        {title} <span style={{ color: "#888", fontWeight: 400 }}>({count})</span>
      </h3>
      {count === 0 ? <p style={{ color: "#888", margin: 0 }}>없음</p> : children}
    </div>
  );
}

type LastCaptureBatch = {
  id: string;
  created: number;
  merged: number;
  policies: number;
  consultations: number;
  at: number;
};

function CapturePanel({ onOpenCustomer, onChanged }: { onOpenCustomer: (id: string) => void; onChanged: () => void }) {
  const [text, setText] = useState("");
  const [context, setContext] = useState(""); // 업로드 파일에 대한 설명·요청 (분석에만 반영, 저장 안 함)
  const [staged, setStaged] = useState<File | null>(null); // 끌어다 놓은/고른 파일 — 바로 분석 안 하고 대기
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string | null>(null); // 진행 중 안내 문구
  const [rows, setRows] = useState<any[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lastBatch, setLastBatch] = useState<LastCaptureBatch | null>(null);
  const [undoConfirm, setUndoConfirm] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [sttStatus, setSttStatus] = useState<SttStatus | null>(null);
  const busyRef = useRef(false);
  const sttPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollGenRef = useRef(0);
  const pollDeadlineRef = useRef<number | null>(null);
  const autoRetryUsedRef = useRef(false);
  const batchRef = useRef<string | null>(null);
  const analyzeRef = useRef<(file?: File, autoRetry?: boolean) => Promise<void>>(async () => {});

  const stopSttPoll = () => {
    if (sttPollRef.current) clearTimeout(sttPollRef.current);
    sttPollRef.current = null;
    pollGenRef.current++;
  };

  const pollStt = (file?: File) => {
    stopSttPoll();
    const gen = pollGenRef.current;
    if (pollDeadlineRef.current === null) pollDeadlineRef.current = Date.now() + 15 * 60 * 1000;
    const poll = async () => {
      if (gen !== pollGenRef.current) return;
      if (Date.now() >= pollDeadlineRef.current!) {
        stopSttPoll();
        setBusy(false);
        setPhase(null);
        setSttStatus((s) => ({ ...(s || { pct: null, mb: 0, total_mb: 0, backend: "faster" }), state: "failed", reason: "timeout" }));
        return;
      }
      try {
        const r = await engineFetch("/stt/status");
        if (gen !== pollGenRef.current) return;
        if (!r.ok) return;
        const next = (await r.json()) as SttStatus;
        if (gen !== pollGenRef.current) return;
        setSttStatus(next);
        if (next.state === "ready") {
          stopSttPoll();
          setSttStatus(null);
          if (file && !busyRef.current && !autoRetryUsedRef.current) {
            autoRetryUsedRef.current = true;
            void analyzeRef.current(file, true);
          }
        } else if (next.state === "failed") {
          stopSttPoll();
          setBusy(false);
          setPhase(null);
        }
      } catch {
        // 엔진이 잠시 재시작 중일 수 있으므로 제한 시간 안에서는 계속 확인한다.
      }
      if (gen === pollGenRef.current) sttPollRef.current = setTimeout(poll, 3000);
    };
    sttPollRef.current = setTimeout(poll, 3000);
  };

  useEffect(() => () => stopSttPoll(), []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("iai.lastCaptureBatch");
      const saved = raw ? JSON.parse(raw) as LastCaptureBatch : null;
      if (saved?.id && Date.now() - saved.at < 30 * 60 * 1000) setLastBatch(saved);
      else localStorage.removeItem("iai.lastCaptureBatch");
    } catch {
      localStorage.removeItem("iai.lastCaptureBatch");
    }
  }, []);

  useEffect(() => {
    if (!lastBatch) return;
    const remaining = 30 * 60 * 1000 - (Date.now() - lastBatch.at);
    if (remaining <= 0) {
      setLastBatch(null);
      localStorage.removeItem("iai.lastCaptureBatch");
      return;
    }
    const timer = setTimeout(() => {
      setLastBatch(null);
      setUndoConfirm(false);
      localStorage.removeItem("iai.lastCaptureBatch");
    }, remaining);
    return () => clearTimeout(timer);
  }, [lastBatch]);

  useEffect(() => {
    stopSttPoll();
    pollDeadlineRef.current = null;
    autoRetryUsedRef.current = false;
    if (!staged || !AUDIO_RE.test(staged.name)) {
      setSttStatus(null);
      return;
    }
    void engineFetch("/stt/status").then(async (r) => {
      if (!r.ok) return;
      const next = (await r.json()) as SttStatus;
      setSttStatus(next.state === "ready" ? null : next);
    }).catch(() => {});
  }, [staged]);

  const reset = () => {
    setRows([]);
    setText("");
    setContext("");
    setStaged(null);
    setMsg(null);
  };

  const api = async (path: string, init: RequestInit) => {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (batchRef.current) headers.set("X-Import-Batch", batchRef.current);
    const r = await engineFetch(path, {
      ...init,
      headers,
    });
    if (!r.ok) {
      const body = await r.json().catch(() => null);
      const d = body?.detail;
      const msg = Array.isArray(d)
        ? d.map((e: any) => (e && typeof e === "object" && "msg" in e ? e.msg : String(e))).join(" / ")
        : d;
      const error = new Error(msg ?? `HTTP ${r.status}`);
      (error as any).status = r.status;
      (error as any).body = body;
      throw error;
    }
    return r.status === 204 ? null : r.json();
  };

  const analyze = async (file?: File, autoRetry = false) => {
    if (!file && !text.trim()) return;
    const isAudio = !!file && AUDIO_RE.test(file.name);
    setBusy(true);
    busyRef.current = true;
    const duration = isAudio && file ? await audioDuration(file) : null;
    setPhase(
      isAudio
        ? sttPhrase(sttStatus?.backend || "faster", duration)
        : file
        ? "📄 파일에서 고객 정보 분석 중…"
        : "텍스트 분석 중…",
    );
    setErr(null);
    setMsg(null);
    setRows([]);
    try {
      const fd = new FormData();
      if (text.trim()) fd.append("text", text.trim());
      if (context.trim()) fd.append("context", context.trim());
      if (file) fd.append("file", file);
      const b = await api("/capture", { method: "POST", body: fd });
      setRows(
        (b.items as any[]).map((it) => {
          const pols: PolicyDraft[] = it.policies || [];
          return {
            ...it,
            warnings: rrnEnabled()
              ? it.warnings
              : (it.warnings || []).filter((w: string) => !String(w).includes("주민")),
            name: it.fields?.name ?? "",
            phone: it.fields?.phone ?? "",
            rrn: it.fields?.rrn ?? "",
            action: it.match ? "merge" : "new",
            policies: pols,
            policyChecked: pols.map((p) => !!(p.insurer || p.product_name)),
          };
        }),
      );
      setContext(""); // 분석에 반영됐으니 비운다
      setStaged(null);
    } catch (e) {
      if ((e as any)?.status === 409 && file) {
        setSttStatus((e as any).body?.stt_status || null);
        if (autoRetry) {
          stopSttPoll();
          setSttStatus((s) => ({ ...(s || { pct: null, mb: 0, total_mb: 0, backend: "faster" }), state: "failed", reason: "timeout" }));
          return;
        }
        setPhase("음성 인식 파일을 준비하고 있습니다. 다 받으면 분석이 자동으로 시작됩니다.");
        pollStt(file);
        return;
      }
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      busyRef.current = false;
      setPhase(null);
    }
  };
  analyzeRef.current = analyze;

  const retryStt = async () => {
    setErr(null);
    pollDeadlineRef.current = null;
    autoRetryUsedRef.current = false;
    try {
      const r = await engineFetch("/stt/prewarm", { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const next = (await r.json()) as SttStatus;
      setSttStatus(next);
      pollStt(staged || undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const pickFile = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,.m4a,.mp3,.wav,.aac,.aiff,.pdf,.txt,.csv,.md,text/plain";
    const f: File | null = await new Promise((resolve) => {
      input.onchange = () => resolve(input.files?.[0] ?? null);
      input.click();
    });
    if (f) setStaged(f); // 바로 분석하지 않고 설명을 받는다
  };

  const patchRow = (i: number, p: Record<string, unknown>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));

  const run = async () => {
    batchRef.current = crypto.randomUUID().replace(/-/g, "");
    setErr(null);
    setMsg(null);
    setBusy(true);
    setPhase("저장 중…");
    const res = { created: 0, merged: 0, skipped: 0, failed: 0, policies: 0, consultations: 0 };
    let lastNewId: string | null = null;
    let mergedId: string | null = null;
    const policyFails: { label: string; error: string }[] = [];
    let rrnErr: string | null = null;
    let saveErr: string | null = null;
    for (const row of rows) {
      if (row.action === "skip") {
        res.skipped++;
        continue;
      }
      try {
        const f = row.fields ?? {};
        const rd = String(row.rrn ?? f.rrn ?? "").replace(/\D/g, "");
        let cid: string | null = null;
        if (row.action === "merge" && row.match) {
          const ex = row.match;
          const patch: Record<string, string> = {};
          // 사용자가 행에서 직접 보고 고칠 수 있는 이름·전화만 덮어쓴다.
          for (const k of ["name", "phone"]) {
            const v = String(k === "phone" ? row.phone : row.name).trim();
            if (v && v !== (ex[k] || "")) patch[k] = v;
          }
          // 화면에 안 보이는 필드(OCR/LLM 추출값)는 기존이 비어 있을 때만 채운다 — 정상 데이터 훼손 방지.
          for (const k of ["birth_date", "gender", "email", "address", "occupation"]) {
            const v = String(f[k] || "").trim();
            if (v && !(ex[k] || "").trim()) patch[k] = v;
          }
          const extraMemo = String(f.memo || "").trim();
          if (extraMemo && !(ex.memo || "").includes(extraMemo)) {
            const stamp = new Date().toISOString().slice(0, 10);
            patch.memo = (ex.memo ? ex.memo + "\n" : "") + `[${stamp}] ${extraMemo}`;
          }
          if (Object.keys(patch).length)
            await api(`/customers/${ex.id}`, { method: "PATCH", body: JSON.stringify(patch) });
          // 주민번호는 별도 PATCH — 형식 검증(422)이 위 필드 갱신을 되돌리지 않게.
          if (rd.length === 13) {
            try {
              await api(`/customers/${ex.id}`, { method: "PATCH", body: JSON.stringify({ rrn: rd }) });
            } catch (e) {
              rrnErr = e instanceof Error ? e.message : String(e);
            }
          } else if (String(row.rrn ?? "").trim()) {
            rrnErr = `주민번호가 13자리가 아니어서 저장하지 않았습니다: ${row.rrn}`;
          }
          if (row.consultation) {
            await api(`/customers/${ex.id}/consultations`, { method: "POST", body: JSON.stringify(row.consultation) });
            res.consultations++;
          }
          mergedId = ex.id;
          cid = ex.id;
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
          lastNewId = created.id;
          if (rd.length === 13) {
            try {
              await api(`/customers/${created.id}`, { method: "PATCH", body: JSON.stringify({ rrn: rd }) });
            } catch (e) {
              rrnErr = e instanceof Error ? e.message : String(e);
            }
          } else if (String(row.rrn ?? "").trim()) {
            rrnErr = `주민번호가 13자리가 아니어서 저장하지 않았습니다: ${row.rrn}`;
          }
          if (row.consultation) {
            await api(`/customers/${created.id}/consultations`, { method: "POST", body: JSON.stringify(row.consultation) });
            res.consultations++;
          }
          cid = created.id;
          res.created++;
        }
        // 보험 문서에서 뽑은 계약(들)을 고객 가입목록에 추가
        if (cid && row.policies?.length) {
          const r = await savePolicies(api, cid, row.policies, row.policyChecked || []);
          res.policies += r.saved;
          policyFails.push(...r.failures.map(({ label, error }) => ({ label, error })));
        }
      } catch (e) {
        res.failed++;
        saveErr = saveErr || (e instanceof Error ? e.message : String(e));
      }
    }
    setBusy(false);
    setPhase(null);
    track("capture_saved", {
      created: res.created,
      merged: res.merged,
      skipped: res.skipped,
      policies: res.policies,
    });
    let completedBatch: LastCaptureBatch = {
      id: batchRef.current!,
      created: res.created,
      merged: res.merged,
      policies: res.policies,
      consultations: res.consultations,
      at: Date.now(),
    };
    try {
      const batchesResponse = await engineFetch("/capture/batches?limit=1");
      if (batchesResponse.ok) {
        const latest = (await batchesResponse.json()).batches?.[0];
        if (latest?.id === completedBatch.id) {
          completedBatch = {
            ...completedBatch,
            created: latest.counts.customers,
            policies: latest.counts.policies,
            consultations: latest.counts.consultations,
          };
        }
      }
    } catch {
      // 서버 배치 요약 조회 실패 시 위의 프론트 집계를 사용한다.
    }
    setLastBatch(completedBatch);
    localStorage.setItem("iai.lastCaptureBatch", JSON.stringify(completedBatch));
    batchRef.current = null;
    const total = res.created + res.merged + res.skipped + res.failed;
    const onlyNew = res.created === 1 && total === 1;
    const onlyMerge = res.merged === 1 && total === 1;
    const single = (onlyNew && lastNewId) || (onlyMerge && mergedId);
    if (single && !policyFails.length && !rrnErr && !saveErr) {
      reset();
      onOpenCustomer((lastNewId || mergedId)!);
      return;
    }
    reset();
    setMsg(
      `완료 — 신규 ${res.created} · 갱신 ${res.merged} · 건너뜀 ${res.skipped}` +
        (res.policies ? ` · 보험계약 ${res.policies}` : "") +
        (res.failed ? ` · 실패 ${res.failed}` : ""),
    );
    const errs = [
      saveErr && "저장 실패: " + saveErr,
      policyFails.length &&
        `계약 저장 실패 ${policyFails.length}건:\n` +
          policyFails.map((f) => `· ${f.label} — ${f.error}`).join("\n"),
      rrnErr && "주민번호 저장 실패: " + rrnErr,
    ].filter(Boolean);
    if (errs.length) setErr(errs.join(" / "));
  };

  const undoLastBatch = async () => {
    if (!lastBatch) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await engineFetch(`/capture/batches/${lastBatch.id}/undo`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      setLastBatch(null);
      setUndoConfirm(false);
      localStorage.removeItem("iai.lastCaptureBatch");
      if (r.status === 410) setErr("되돌릴 수 있는 시간이 지났습니다.");
      else if (r.status === 404) setMsg("되돌릴 항목이 없습니다.");
      else if (!r.ok) setErr(d.detail || `HTTP ${r.status}`);
      else {
        setMsg(`되돌렸습니다 — 고객 ${d.deleted.customers} · 보험계약 ${d.deleted.policies} · 상담 ${d.deleted.consultations}`);
        onChanged();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const reasonLabel = (r: string | null) =>
    r === "phone" ? "전화 일치" : r === "name+birthdate" ? "동일인" : r === "name" ? "동명" : "";

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!busy) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (busy) return;
        const f = e.dataTransfer.files?.[0];
        if (f) setStaged(f); // 바로 분석하지 않고 설명을 받는다
      }}
      style={{
        border: dragOver ? "2px dashed #2563eb" : "1px solid #2563eb",
        borderRadius: 8,
        padding: 12,
        marginBottom: 16,
        background: dragOver ? "#e7efff" : "#f5f8ff",
      }}
    >
      {lastBatch && Date.now() - lastBatch.at < 30 * 60 * 1000 && (
        <div style={{ border: "1px solid #9ac", borderRadius: 6, padding: 8, marginBottom: 10, background: "#fff" }}>
          {!undoConfirm ? (
            <span>
              방금 등록: 신규 {lastBatch.created}명 · 갱신 {lastBatch.merged}명 · 보험계약 {lastBatch.policies}건 · 상담 {lastBatch.consultations}건{" "}
              <button onClick={() => setUndoConfirm(true)} disabled={busy}>되돌리기</button>
            </span>
          ) : (
            <span>
              방금 등록한 고객 {lastBatch.created}명과 보험계약 {lastBatch.policies}건 · 상담 {lastBatch.consultations}건이 지워집니다. 갱신한 기존 고객 {lastBatch.merged}명은 지워지지 않지만, 이번에 채워진 정보는 그대로 남습니다. 이번에 새로 등록된 주민번호가 있으면 함께 제거됩니다.{" "}
              <button onClick={undoLastBatch} disabled={busy}>되돌리기 확인</button>{" "}
              <button onClick={() => setUndoConfirm(false)} disabled={busy}>취소</button>
            </span>
          )}
        </div>
      )}
      <h3 style={{ margin: "0 0 6px" }}>던져넣기 — 한 명이든 여러 명이든, 녹취·PDF·텍스트 무엇이든</h3>
      {staged ? (
        <div
          style={{
            border: "1px solid #2563eb",
            borderRadius: 8,
            padding: 10,
            background: "#fff",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <span style={{ fontWeight: 600, color: "#161" }}>📎 {staged.name}</span>
            <button onClick={() => setStaged(null)} disabled={busy} style={{ fontSize: 12 }}>
              파일 빼기
            </button>
          </div>
          <textarea
            style={{ width: "100%", boxSizing: "border-box", minHeight: 72, padding: 6 }}
            placeholder={
              "이 파일에 대해 알려주세요 — 누구 자료인지(고객 이름), 어느 보험사, 어떤 문서(제안서·증권·보장분석·녹취)인지, 특별히 봐야 할 점.\n여기 적은 내용이 분석에 함께 들어갑니다. (저장은 안 됩니다)"
            }
            value={context}
            onChange={(e) => setContext(e.target.value)}
            autoFocus
          />
          <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            <button
              onClick={() => analyze(staged)}
              disabled={busy}
              style={{ fontWeight: 700, background: "#2563eb", color: "#fff", border: "none", borderRadius: 5, padding: "6px 16px" }}
            >
              {busy ? "분석 중…" : "이 파일 분석"}
            </button>
            <button onClick={() => { setStaged(null); setContext(""); }} disabled={busy}>
              취소
            </button>
          </div>
        </div>
      ) : (
        <>
          <textarea
            style={{ width: "100%", boxSizing: "border-box", minHeight: 60, padding: 6 }}
            placeholder={"고객 정보를 붙여넣으세요. 여러 명이면 한 줄에 한 명씩.\n또는 녹취·PDF·텍스트 파일을 이 영역에 끌어다 놓거나 [📎 파일] 버튼을 쓰세요."}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
            <button onClick={() => analyze()} disabled={busy}>
              {busy ? "분석 중…" : "분석"}
            </button>
            <button onClick={pickFile} disabled={busy}>
              {busy ? "…" : "📎 파일 (녹취·PDF·텍스트)"}
            </button>
            {rows.length > 0 && !busy && (
              <button onClick={reset} style={{ marginLeft: "auto" }}>
                취소
              </button>
            )}
          </div>
        </>
      )}
      {dragOver && (
        <p style={{ color: "#2563eb", fontSize: 12, margin: "6px 0 0" }}>
          여기에 놓으면 파일을 붙입니다 (바로 분석 안 함 — 설명을 적고 [이 파일 분석])
        </p>
      )}
      {phase && (
        <div className="ai-analyzing" role="status" aria-live="polite">
          <span style={{ fontSize: 16 }}>⏳</span>
          <span>{phase}</span>
        </div>
      )}
      {sttStatus && sttStatus.state !== "ready" && (
        <div role="status" aria-live="polite" style={{ marginTop: 8, fontSize: 13, color: sttStatus.state === "failed" ? "#b00" : "#555" }}>
          {sttStatus.state === "downloading" ? (
            <span>
              처음 한 번만 받는 음성 인식 파일을 내려받고 있습니다.
              {sttStatus.pct != null ? ` ${sttStatus.pct}%` : ""}
              {sttStatus.total_mb > 0 ? ` (${sttStatus.mb}MB / ${sttStatus.total_mb}MB)` : sttStatus.mb > 0 ? ` (${sttStatus.mb}MB)` : ""}
              {" — 다 받으면 분석이 자동으로 시작됩니다."}
            </span>
          ) : sttStatus.reason === "network" ? (
            <span>인터넷이 연결되지 않아 음성 인식 파일을 받지 못했습니다. 연결한 뒤 [다시 시도]를 눌러 주세요.</span>
          ) : sttStatus.reason === "blocked" ? (
            <span>회사 네트워크가 내려받기를 막고 있는 것 같습니다. 휴대폰 테더링 같은 다른 인터넷에서 한 번만 받으면, 이후에는 다시 받지 않습니다.</span>
          ) : sttStatus.reason === "disk" ? (
            <span>저장 공간이 부족합니다. 1GB 이상 비운 뒤 [다시 시도]를 눌러 주세요.</span>
          ) : sttStatus.reason === "timeout" ? (
            <span>음성 인식 파일 준비가 오래 걸려 자동 시도를 멈췄습니다. [다시 시도]를 눌러 주세요.</span>
          ) : sttStatus.state === "failed" ? (
            <span>음성 인식 준비에 실패했습니다. [다시 시도]를 눌러 주세요. 계속 안 되면 설정 화면의 진단 내용을 보내 주세요.</span>
          ) : (
            <span>음성 인식 파일을 준비하고 있습니다.</span>
          )}
          {(sttStatus.state === "failed" || sttStatus.state === "absent") && (
            <button onClick={retryStt} style={{ marginLeft: 8 }}>다시 시도</button>
          )}
        </div>
      )}
      {msg && <p style={{ color: "#161", fontSize: 12 }}>{msg}</p>}
      {err && <p style={{ color: "#b00", fontSize: 13, whiteSpace: "pre-line" }}>오류: {err}</p>}

      {rows.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 12, color: "#555", marginBottom: 4 }}>
            {rows.length}명 — 각 행의 동작을 확인하고 [저장]
          </div>
          {rows.map((row, i) => (
            <div key={i} style={{ padding: "4px 0", borderBottom: "1px solid #dde3ee", fontSize: 13 }}>
              <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
                <select value={row.action} onChange={(e) => patchRow(i, { action: e.target.value })} style={{ fontSize: 12 }}>
                  <option value="new">신규 등록</option>
                  {row.match && <option value="merge">기존 「{row.match.name}」 갱신</option>}
                  <option value="skip">건너뜀</option>
                </select>
                <input style={{ padding: 3, width: 90 }} value={row.name} onChange={(e) => patchRow(i, { name: e.target.value })} placeholder="이름" />
                <input style={{ padding: 3, width: 120 }} value={row.phone} onChange={(e) => patchRow(i, { phone: e.target.value })} placeholder="전화" />
                {rrnEnabled() && (
                  <input style={{ padding: 3, width: 130 }} value={row.rrn} onChange={(e) => patchRow(i, { rrn: e.target.value })} placeholder="주민번호 13자리" />
                )}
                {row.match && <span style={{ fontSize: 11, color: "#8a4b00" }}>{reasonLabel(row.match_reason)}</span>}
                {row.consultation && (
                  <span style={{ fontSize: 11, color: "#161" }}>
                    {row.consultation.channel && row.consultation.channel !== "전화 녹취"
                      ? `📄 ${row.consultation.channel} 이력`
                      : "📞통화"}
                  </span>
                )}
                {row.warnings?.length > 0 && (
                  <span style={{ fontSize: 11, color: "#b00" }} title={row.warnings.join(" / ")}>⚠</span>
                )}
              </div>
              {row.action !== "skip" && row.policies?.length > 0 && (
                <PolicyList
                  policies={row.policies}
                  checked={row.policyChecked || []}
                  docType={row.doc_type}
                  onToggle={(pi, v) =>
                    patchRow(i, {
                      policyChecked: (row.policyChecked || []).map((c: boolean, j: number) => (j === pi ? v : c)),
                    })
                  }
                  onEdit={(pi, patch) =>
                    patchRow(i, {
                      policies: row.policies.map((p: PolicyDraft, j: number) => (j === pi ? { ...p, ...patch } : p)),
                    })
                  }
                />
              )}
              {row.action !== "skip" && row.coverage_status?.length > 0 && (
                <CoverageTable rows={row.coverage_status} />
              )}
            </div>
          ))}
          <button onClick={run} disabled={busy} style={{ marginTop: 6, fontWeight: 600 }}>
            {busy ? "처리 중…" : rows.length > 1 ? `${rows.length}명 저장` : "저장"}
          </button>
        </div>
      )}
    </div>
  );
}

function HomeScreen({ onOpenCustomer }: { onOpenCustomer: (id: string) => void }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ownOnly, setOwnOnly] = useState(() => localStorage.getItem("iai.own_only") === "1");

  const load = useCallback(async () => {
    try {
      const qs = ownOnly ? "?own=1" : "";
      setData(await (await engineFetch(`/dashboard${qs}`)).json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [ownOnly]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const row: React.CSSProperties = {
    padding: "6px 0",
    borderBottom: "1px solid #eee",
    cursor: "pointer",
    display: "flex",
    justifyContent: "space-between",
    gap: 8,
  };
  const badge = (b: { label: string; color: string } | null) =>
    b ? (
      <span style={{ color: "#fff", background: b.color, borderRadius: 4, padding: "1px 6px", fontSize: 11 }}>
        {b.label}
      </span>
    ) : null;

  const ddayFromDays = (days: number): { label: string; color: string } => {
    if (days < 0) return { label: `${-days}일 지남`, color: "#b00" };
    if (days === 0) return { label: "오늘", color: "#c60" };
    return { label: `D-${days}`, color: days <= 7 ? "#c60" : "#888" };
  };

  const completeFollowUp = async (kid: string) => {
    try {
      const r = await engineFetch(`/consultations/${kid}/follow-up/complete`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div style={{ maxWidth: 620, margin: "24px auto", padding: "0 16px" }}>
      <h2 style={{ marginTop: 0 }}>홈</h2>
      <EngineStrip />
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, margin: "8px 0" }} title="계약 수·만기 임박을 내가 직접 가입시킨 계약으로만">
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
      <CapturePanel onOpenCustomer={onOpenCustomer} onChanged={load} />
      {error && <p style={{ color: "#b00" }}>대시보드 오류: {error}</p>}
      {!data ? (
        <p style={{ color: "#888" }}>불러오는 중…</p>
      ) : (
        <>
          <div style={{ display: "flex", gap: 16, marginBottom: 16, fontSize: 14 }}>
            <span>고객 <b>{data.counts.customers}</b></span>
            <span>보험계약 <b>{data.counts.active_policies}</b>/{data.counts.policies}</span>
            <span>상담 <b>{data.counts.consultations}</b></span>
          </div>

          <Card title="후속 연락 예정 (7일 내)" count={data.follow_ups.length}>
            {data.follow_ups.map((f) => (
              <div key={f.id} style={row} onClick={() => onOpenCustomer(f.customer_id)}>
                <span>
                  {f.customer_name ?? "(고객)"} — {f.title ?? "상담"}
                </span>
                <span style={{ display: "flex", gap: 6, whiteSpace: "nowrap", alignItems: "center" }}>
                  {badge(dday(f.follow_up_at))}
                  <button
                    style={{ fontSize: 11 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      completeFollowUp(f.id);
                    }}
                  >
                    완료
                  </button>
                </span>
              </div>
            ))}
          </Card>

          <Card title="만기 임박 계약 (30일 내)" count={data.expiring_policies.length}>
            {data.expiring_policies.map((p) => (
              <div key={p.id} style={row} onClick={() => onOpenCustomer(p.customer_id)}>
                <span>
                  {p.customer_name ?? "(고객)"} — {p.insurer ?? ""} {p.product_name ?? ""}
                </span>
                <span style={{ display: "flex", gap: 6, whiteSpace: "nowrap", alignItems: "center" }}>
                  {p.end_date}
                  <span style={{ color: "#888", fontSize: 11 }}>
                    {p.end_date_derived === 1 ? "자동" : p.end_date_derived === 0 ? "직접" : ""}
                  </span>
                  {badge(dday(p.end_date))}
                </span>
              </div>
            ))}
          </Card>

          <Card title="생일 임박 (30일 내)" count={data.birthdays.length}>
            {data.birthdays.map((b) => (
              <div key={b.id} style={row} onClick={() => onOpenCustomer(b.id)}>
                <span>
                  {b.name}
                  {b.estimated && (
                    <span style={{ color: "#888", background: "#eee", borderRadius: 4, padding: "1px 5px", fontSize: 11, marginLeft: 6 }}>
                      추정
                    </span>
                  )}
                  {" — 만 "}
                  {b.turning_age}세
                </span>
                {badge(ddayFromDays(b.days_until))}
              </div>
            ))}
          </Card>

          <Card title="최근 상담" count={data.recent_consultations.length}>
            {data.recent_consultations.map((k) => (
              <div key={k.id} style={row} onClick={() => onOpenCustomer(k.customer_id)}>
                <span>
                  {k.customer_name ?? "(고객)"} — {k.title ?? "상담"}
                </span>
                <span style={{ color: "#888", fontSize: 12, whiteSpace: "nowrap" }}>
                  {(k.consulted_at || "").slice(0, 10)} · {k.channel ?? "-"}
                </span>
              </div>
            ))}
          </Card>
        </>
      )}
    </div>
  );
}

// 작업 D (화면 쪽): PDF를 골라 local-engine /parse/pdf 로 보내고
// 페이지별 추출 결과를 그대로 보여준다. 판단 로직은 없음 — 표시만 (아키텍처 1번 원칙).
function PdfPanel() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doc, setDoc] = useState<ParsedDoc | null>(null);

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 같은 파일 다시 골라도 onChange 뜨도록
    if (!file) return;

    setBusy(true);
    setError(null);
    setDoc(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await engineFetch("/parse/pdf", { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      setDoc((await res.json()) as ParsedDoc);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 480, margin: "20px auto" }}>
      <div style={{ padding: 12, border: "1px solid #ddd", borderRadius: 8 }}>
        <p style={{ marginTop: 0 }}>
          <strong>약관 PDF 열기</strong>
        </p>
        <input type="file" accept="application/pdf,.pdf" onChange={onPick} disabled={busy} />
        {busy && <p>추출 중...</p>}
        {error && <p style={{ color: "#b00" }}>추출 실패: {error}</p>}

        {doc && (
          <div style={{ marginTop: 12 }}>
            <p style={{ margin: "4px 0" }}>
              {doc.filename} — 총 {doc.page_count}페이지
            </p>
            <div style={{ maxHeight: 360, overflowY: "auto", borderTop: "1px solid #eee" }}>
              {doc.pages.map((p) => (
                <div key={p.page} style={{ padding: "8px 0", borderBottom: "1px solid #eee" }}>
                  <div style={{ fontSize: 12, color: "#888" }}>
                    p.{p.page} · {p.char_count}자
                    {p.tables.length > 0 && ` · 표 ${p.tables.length}개`}
                    {p.empty && " · (빈 페이지 / 스캔본?)"}
                  </div>
                  <pre style={{ whiteSpace: "pre-wrap", margin: "4px 0 0", fontSize: 13 }}>
                    {p.text || "(텍스트 없음)"}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

async function revealPath(path: string) {
  try {
    await revealItemInDir(path);
  } catch (e) {
    alert("파일 위치:\n" + path);
  }
}

const pboxStyle: React.CSSProperties = {
  border: "1px solid #ddd",
  borderRadius: 8,
  padding: 12,
  marginBottom: 12,
};

function BackupBox() {
  const [items, setItems] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await engineFetch("/backups");
      setItems((await r.json()).backups || []);
    } catch {
      /* 무시 */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const backupNow = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await engineFetch("/maintenance/backup", { method: "POST" });
      const b = await r.json();
      setMsg(
        b.created
          ? `백업 완료: ${b.filename}${b.pruned ? ` (오래된 ${b.pruned}개 정리)` : ""}`
          : `백업하지 못했습니다${b.reason ? `: ${b.reason}` : " (백업할 DB 없음)"}`,
      );
      // usage 기록은 엔진(POST /maintenance/backup)이 정본. 프론트 중복 기록 안 함.
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const restore = async (filename: string) => {
    if (!confirm(`'${filename}' 로 되돌립니다.\n현재 데이터는 안전 사본으로 보관되며, 복구 후 앱을 다시 시작해야 합니다.\n계속할까요?`)) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await engineFetch("/backups/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      const b = await r.json();
      setMsg(`복구했습니다. 안전 사본: ${b.safety_copy}. 지금 앱(엔진)을 다시 시작하세요.`);
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={pboxStyle}>
      <b>백업</b>
      <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>
        고객 DB 스냅샷. 앱 시작 시 하루 1회 자동 백업되고, 최신 20개를 보관합니다.
      </p>
      <p style={{ fontSize: 12, color: "#555", margin: "2px 0" }}>
        보관 중 {items.length}개
        {items[0] ? ` · 최근 ${items[0].created_at}` : " · 아직 없음"}
      </p>
      <button onClick={backupNow} disabled={busy}>
        {busy ? "처리 중…" : "지금 백업"}
      </button>
      {msg && <p style={{ fontSize: 12, color: "#161", margin: "6px 0" }}>{msg}</p>}
      <div style={{ marginTop: 8, maxHeight: 180, overflowY: "auto" }}>
        {items.length === 0 && <p style={{ fontSize: 12, color: "#999" }}>백업 없음</p>}
        {items.map((it) => (
          <div key={it.filename} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, padding: "2px 0" }}>
            <span style={{ flex: 1 }}>
              {it.filename} <span style={{ color: "#999" }}>· {Math.round(it.size / 1024)}KB · {it.created_at}</span>
              {it.kind === "safety" && <span style={{ color: "#8a4b00" }}> (안전 사본)</span>}
            </span>
            <button style={{ fontSize: 11 }} onClick={() => revealPath(it.path)}>폴더 열기</button>
            <button style={{ fontSize: 11 }} disabled={busy} onClick={() => restore(it.filename)}>복구</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ExportBox() {
  const [items, setItems] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await engineFetch("/exports");
      setItems((await r.json()).exports || []);
    } catch {
      /* 무시 */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const makeZip = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await engineFetch("/export");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const b = await r.json();
      setLast(b);
      // usage 기록은 엔진(GET /export)이 정본. 프론트 중복 기록 안 함.
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={pboxStyle}>
      <b>데이터 내보내기</b>
      <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>
        고객·계약·상담을 CSV 3개 + README 로 묶어 zip 으로 저장합니다 (UTF-8 BOM, 엑셀에서 바로 열림).
        주민등록번호는 입력 기능이 켜져 있을 때만 포함됩니다.
      </p>
      <button onClick={makeZip} disabled={busy}>
        {busy ? "만드는 중…" : "내보내기 파일 만들기"}
      </button>
      {err && <p style={{ fontSize: 12, color: "#b00" }}>{err}</p>}
      {last && (
        <p style={{ fontSize: 12, color: "#161", margin: "6px 0" }}>
          {last.filename} — {last.rows}행
          {last.decrypt_failures ? ` · 복호화 실패 ${last.decrypt_failures}` : ""}{" "}
          <button style={{ fontSize: 11 }} onClick={() => revealPath(last.path)}>폴더 열기</button>
        </p>
      )}
      <div style={{ marginTop: 8, maxHeight: 140, overflowY: "auto" }}>
        {items.map((it) => (
          <div key={it.filename} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, padding: "2px 0" }}>
            <span style={{ flex: 1 }}>
              {it.filename} <span style={{ color: "#999" }}>· {Math.round(it.size / 1024)}KB · {it.created_at}</span>
            </span>
            <button style={{ fontSize: 11 }} onClick={() => revealPath(it.path)}>폴더 열기</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function UsageLogBox() {
  const [mode, setMode] = useState<"raw" | "daily">("daily");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const exportCsv = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await engineFetch(`/usage/export?mode=${mode}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `usage-${mode}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={pboxStyle}>
      <b>사용 로그</b>
      <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>
        어떤 기능을 얼마나 쓰는지 파악하기 위한 익명 집계입니다. 이름·내용은 저장하지 않습니다.
        서버로 자동 전송되지 않으며, 아래 버튼으로 직접 내보낼 때만 파일이 만들어집니다.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <select value={mode} onChange={(e) => setMode(e.target.value as "raw" | "daily")} style={{ fontSize: 12 }}>
          <option value="daily">일자별 집계</option>
          <option value="raw">원본 이벤트</option>
        </select>
        <button onClick={exportCsv} disabled={busy}>
          {busy ? "내보내는 중…" : "CSV 내보내기"}
        </button>
      </div>
      {err && <p style={{ fontSize: 12, color: "#b00" }}>{err}</p>}
    </div>
  );
}

function PilotTools() {
  return (
    <>
      <BackupBox />
      <ExportBox />
      <div style={pboxStyle}>
        <b>데이터 가져오기</b>
        <ImportWizard />
      </div>
      <UsageLogBox />
    </>
  );
}

function DiagnosticsScreen({ onOpenCustomer }: { onOpenCustomer: (id: string) => void }) {
  const [d, setD] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lic, setLic] = useState<Record<string, any> | null>(null);
  const [devices, setDevices] = useState<any[]>([]);
  const [recompBusy, setRecompBusy] = useState(false);
  const [recomp, setRecomp] = useState<Record<string, any> | null>(null);
  const [rrnBusy, setRrnBusy] = useState(false);
  const [me, setMe] = useState<{ id: string; email: string; created_at?: string } | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordErr, setPasswordErr] = useState<string | null>(null);
  const [passwordToast, setPasswordToast] = useState<string | null>(null);
  const passwordDetails = useRef<HTMLDetailsElement>(null);
  const passwordToastTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (passwordToastTimer.current) window.clearTimeout(passwordToastTimer.current);
  }, []);

  const load = useCallback(async () => {
    try {
      setD(await (await engineFetch("/diagnostics")).json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    try {
      const r = await resolveLicense();
      setLic(r ? { ...r.state, offline: r.offline } : null);
      setDevices(await listDevices());
    } catch {
      /* 오프라인이면 생략 */
    }
    try {
      setMe(await fetchMe());
    } catch {
      /* 오프라인이면 저장된 사용자 정보만 표시 */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const toggleRrn = async (next: boolean) => {
    setRrnBusy(true);
    setErr(null);
    try {
      const r = await engineFetch("/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rrn_input_enabled: next }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => null);
        const locked = b?.detail?.locked_by === "env" || b?.detail === "locked";
        throw new Error(
          locked
            ? (typeof b?.detail === "object" && b.detail.message) ||
              "환경변수로 잠겨 있어 변경할 수 없습니다."
            : `HTTP ${r.status}`
        );
      }
      await loadEngineSettings(); // localStorage["iai.settings"] 갱신 → rrnEnabled() 반영
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRrnBusy(false);
    }
  };

  const recomputeExpiry = async () => {
    setRecompBusy(true);
    setErr(null);
    try {
      const r = await engineFetch("/maintenance/recompute-expiry", { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRecomp(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRecompBusy(false);
    }
  };

  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordErr(null);
    if (newPassword.length < 6) {
      setPasswordErr("새 비밀번호는 6자 이상이어야 합니다.");
      return;
    }
    if (newPassword !== newPasswordConfirm) {
      setPasswordErr("새 비밀번호가 일치하지 않습니다.");
      return;
    }
    setPasswordBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setNewPasswordConfirm("");
      if (passwordDetails.current) passwordDetails.current.open = false;
      setPasswordToast("비밀번호를 변경했습니다");
      if (passwordToastTimer.current) window.clearTimeout(passwordToastTimer.current);
      passwordToastTimer.current = window.setTimeout(() => setPasswordToast(null), 3000);
    } catch (e) {
      setPasswordErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPasswordBusy(false);
    }
  };

  const box: React.CSSProperties = { border: "1px solid #ddd", borderRadius: 8, padding: 12, marginBottom: 12 };
  const kv = (k: string, v: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: 13, padding: "2px 0" }}>
      <span style={{ width: 130, color: "#888" }}>{k}</span>
      <span>{v}</span>
    </div>
  );

  return (
    <div style={{ maxWidth: 620, margin: "24px auto", padding: "0 16px" }}>
      <h2 style={{ marginTop: 0 }}>설정 · 진단</h2>
      <button onClick={() => void checkForUpdate()} style={{ marginBottom: 12 }}>
        업데이트 확인
      </button>
      {err && <p style={{ color: "#b00" }}>{err}</p>}
      {!d ? (
        <p style={{ color: "#888" }}>불러오는 중…</p>
      ) : (
        <>
          <div style={box}>
            <b>엔진</b>
            {kv("Local Engine", d.engine?.local_engine ?? "-")}
            {kv("Ollama", d.engine?.ollama === "connected" ? "연결됨 ✅" : "연결 안됨 ❌")}
            {kv("모델", (d.engine?.models ?? []).join(", ") || "-")}
          </div>
          <div style={box}>
            <b>고객 DB 암호화</b>
            {kv("방식", d.customer_db?.encryption)}
            {kv("키 보관", d.customer_db?.key_source)}
          </div>
          <div style={box}>
            <b>주민등록번호 입력 (파일럿 토글)</b>
            {kv("현재 상태", d.rrn_input?.enabled ? "켜짐" : "꺼짐")}
            {kv("적용 근거", d.rrn_input?.source === "env" ? "환경변수(잠김)" : d.rrn_input?.source === "local" ? "이 PC 설정" : "기본값(꺼짐)")}
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, marginTop: 6 }}>
              <input
                type="checkbox"
                checked={!!d.rrn_input?.enabled}
                disabled={rrnBusy || !!d.rrn_input?.locked}
                onChange={(e) => toggleRrn(e.target.checked)}
              />
              주민등록번호 입력·표시 기능 사용
            </label>
            {d.rrn_input?.locked && (
              <p style={{ fontSize: 12, color: "#8a4b00", margin: "4px 0 0" }}>
                환경변수 RRN_INPUT_ENABLED 로 고정되어 있어 여기서 바꿀 수 없습니다.
                개인정보보호법상 주민등록번호는 법령에 근거가 있을 때만 수집·보관할 수 있어, 배포 시 관리자가 정책으로 정합니다.
              </p>
            )}
            <p style={{ fontSize: 12, color: "#888", margin: "4px 0 0" }}>
              끄면 입력란이 숨겨지고 응답에서도 제외됩니다. 이미 저장된 값은 암호화된 채 보존됩니다.
            </p>
          </div>
          <div style={box}>
            <b>음성 인식 (Whisper)</b>
            {kv("모델", d.whisper?.model)}
            {kv("백엔드", d.whisper?.backend ?? "-")}
            {kv("상태", d.whisper?.downloaded ? `다운로드됨 (${d.whisper?.size_mb}MB)` : "미다운로드")}
            {kv("실행", `${d.whisper?.device} / ${d.whisper?.compute_type}`)}
            {kv("문서 OCR", d.ocr?.backend === "vision" ? "macOS Vision" : d.ocr?.backend === "rapidocr" ? "RapidOCR (내장)" : "사용 불가")}
            {!String(d.whisper?.backend ?? "").includes("cpp") && (
              <p style={{ fontSize: 12, color: "#8a4b00", margin: "6px 0 0" }}>
                이 PC: 음성 CPU 전사(느림){d.ocr?.backend === "rapidocr" ? " / OCR RapidOCR (첫 실행 시 모델 준비)" : ""}
              </p>
            )}
          </div>

          <div style={box}>
            <b>만기 관리</b>
            <p style={{ fontSize: 12, color: "#888", margin: "4px 0" }}>
              전 계약의 만기일·납입종료일을 보험기간(없으면 memo)에서 다시 계산합니다. 직접 입력한
              만기는 건드리지 않습니다. 여러 번 눌러도 안전합니다.
            </p>
            <button onClick={recomputeExpiry} disabled={recompBusy} style={{ marginTop: 4 }}>
              {recompBusy ? "재계산 중…" : "만기 일괄 재계산"}
            </button>
            {recomp && (
              <div style={{ marginTop: 8, fontSize: 13 }}>
                <p style={{ margin: "4px 0" }}>
                  갱신 {recomp.updated ?? 0} · memo에서 보충 {recomp.filled_from_memo ?? 0} · 직접입력 유지{" "}
                  {recomp.skipped_manual ?? 0}
                  {recomp.status_fixed ? ` · 상태→가입 ${recomp.status_fixed}` : ""}
                  {recomp.failed?.length ? ` · 실패 ${recomp.failed.length}` : ""}
                </p>
                {recomp.need_birthdate?.length > 0 && (
                  <>
                    <p style={{ margin: "6px 0 2px", color: "#8a4b00" }}>
                      생년월일이 없어 만기를 계산 못 한 계약 {recomp.need_birthdate.length}건 — 고객
                      생년월일을 채우세요:
                    </p>
                    <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
                      <thead>
                        <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
                          <th style={{ padding: 4 }}>고객</th>
                          <th style={{ padding: 4 }}>보험기간</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recomp.need_birthdate.map((n: any) => (
                          <tr
                            key={n.policy_id}
                            onClick={() => onOpenCustomer(n.customer_id)}
                            style={{ borderBottom: "1px solid #eee", cursor: "pointer" }}
                          >
                            <td style={{ padding: 4, color: "#2563eb" }}>{n.customer_name ?? "(고객)"}</td>
                            <td style={{ padding: 4 }}>{n.insured_period ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </div>
            )}
          </div>
          <div style={box}>
            <b>데이터</b>
            {kv("고객", d.data?.customers)}
            {kv("보험계약", `${d.data?.active_policies}/${d.data?.policies}`)}
            {kv("상담 이력", d.data?.consultations)}
            {kv("색인된 약관", `${d.rag?.docs?.length ?? 0}개 문서 / ${d.rag?.total_chunks ?? 0} 조각`)}
          </div>

          <div style={box}>
            <b>내 계정</b>
            {kv("이메일", me?.email ?? getUser()?.email ?? "-")}
            {kv("가입일", me?.created_at ? me.created_at.slice(0, 10) : "-")}
            <details ref={passwordDetails} style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>비밀번호 변경</summary>
              <form onSubmit={submitPassword} style={{ marginTop: 8 }}>
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  placeholder="현재 비밀번호"
                  required
                  style={{ display: "block", marginBottom: 6 }}
                />
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="새 비밀번호"
                  required
                  style={{ display: "block", marginBottom: 6 }}
                />
                <input
                  type="password"
                  value={newPasswordConfirm}
                  onChange={(e) => setNewPasswordConfirm(e.target.value)}
                  placeholder="새 비밀번호 확인"
                  required
                  style={{ display: "block", marginBottom: 6 }}
                />
                <button type="submit" disabled={passwordBusy}>
                  {passwordBusy ? "변경 중…" : "변경"}
                </button>
                {passwordErr && <p style={{ fontSize: 12, color: "#b00", margin: "6px 0 0" }}>{passwordErr}</p>}
              </form>
            </details>
          </div>

          <div style={box}>
            <b>라이선스 · 기기</b>
            {lic ? (
              <>
                {kv("플랜", `${lic.plan}${lic.offline ? " (오프라인 확인)" : ""}`)}
                {kv("상태", lic.status === "expired" ? "만료됨 ❌" : "활성 ✅")}
                {kv("만료일", lic.expiry ?? "무기한")}
                {kv("등록 기기", `${devices.length} / ${lic.device_limit}대`)}
                {devices.map((v) => (
                  <div key={v.id} style={{ fontSize: 12, paddingLeft: 130 }}>
                    · {v.name || v.device_id.slice(0, 8)} ({v.app_version || "?"}){" "}
                    <button style={{ fontSize: 11 }} onClick={async () => { await removeDevice(v.id); load(); }}>
                      해제
                    </button>
                  </div>
                ))}
              </>
            ) : (
              kv("상태", "control-server 연결 안 됨")
            )}
          </div>

          <h3 style={{ margin: "20px 0 8px", fontSize: 15 }}>데이터 관리</h3>
          <PilotTools />

          <details style={{ ...box }}>
            <summary style={{ color: "#888", cursor: "pointer" }}>약관 PDF 빠른 확인 (개발용)</summary>
            <PdfPanel />
          </details>
        </>
      )}
      {passwordToast && (
        <div role="status" style={{ position: "fixed", right: 20, bottom: 20, background: "#222", color: "white", padding: "10px 14px", borderRadius: 6 }}>
          {passwordToast}
        </div>
      )}
    </div>
  );
}

// 아키텍처 사이드바: 상담자 홈 / 고객 / 약관 / 상담(V0.2~) / 설정·진단
type View = "home" | "newcustomer" | "customers" | "assistant" | "diagnostics";

function App() {
  const [auth, setAuth] = useState<"checking" | "in" | "out">("checking");
  const [offline, setOffline] = useState(false);
  const [licenseErr, setLicenseErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("home");
  // nonce 를 매번 바꿔서, 같은 고객을 연속으로 열어도 목록 화면이 반응하게 한다
  const [focusCustomer, setFocusCustomer] = useState<{ id: string; nonce: number } | null>(null);
  const [focusListView, setFocusListView] = useState<{ view: "expiry" | "birthday"; nonce: number } | null>(null);
  const [expiringCount, setExpiringCount] = useState(0);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  // CustomersScreen 은 탭 전환("새 고객"↔"고객 목록"↔"AI 문의" 등) 때마다 통째로
  // unmount/mount 되므로, 미저장 분석 초안·분석 진행 여부는 그 화면이 직접 올려보낸 값을 여기서 지킨다.
  // hasDraft(확인 후 이동 가능)와 isAnalyzing(이동 자체를 막음)을 분리해, 내부 목록 이동 가드
  // (Customers.tsx canOpenCustomer)와 상단 탭 가드의 정책을 맞춘다.
  const hasDraftRef = useRef(false);
  const isAnalyzingRef = useRef(false);
  const updateCheckStartedRef = useRef(false);
  // early return(미로그인 등) 이전에 선언해야 하는 훅 — 아래쪽 일반 함수들과 달리 Hook 규칙 적용.
  const handleDraftChange = useCallback((s: { hasDraft: boolean; isAnalyzing: boolean }) => {
    hasDraftRef.current = s.hasDraft;
    isAnalyzingRef.current = s.isAnalyzing;
  }, []);

  const boot = useCallback(async () => {
    void loadEngineSettings();
    track("app_open");
    if (!getToken()) {
      setAuth("out");
      return;
    }
    const lic = await resolveLicense();
    if (!lic) {
      // 토큰은 있으나 서버도 못 붙고 유예도 끝남 → 재로그인
      setAuth("out");
      return;
    }
    setOffline(lic.offline);
    setLicenseErr(lic.state.status === "expired" ? "라이선스가 만료되었습니다. 관리자에게 문의하세요." : null);
    setAuth("in");
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  useEffect(() => {
    // 앱 실행(프로세스) 당 1회만. 로그아웃→재로그인해도 다시 확인하지 않는다.
    if (auth !== "in" || updateCheckStartedRef.current) return;
    updateCheckStartedRef.current = true;
    void checkForUpdate({ silent: true });
  }, [auth]);

  // 앱 로드 시 만기 임박(30일) 계약 수 확인 → 상단 배너
  useEffect(() => {
    if (auth !== "in") return;
    (async () => {
      try {
        const r = await engineFetch("/dashboard");
        const d = await r.json();
        setExpiringCount((d.expiring_policies ?? []).length);
      } catch {
        /* 무시 — 배너만 안 뜸 */
      }
    })();
  }, [auth]);

  if (auth === "checking") {
    return <p style={{ textAlign: "center", marginTop: 80, color: "#888" }}>확인 중…</p>;
  }
  if (auth === "out") {
    return <LoginScreen onLogin={boot} />;
  }

  // 현재 화면(주로 "새 고객"의 던져넣기·보장분석 결과)을 떠나도 되는지 확인.
  // 분석이 실제로 도는 중이면(isAnalyzing) 이동 자체를 막고(내부 목록 가드와 동일 정책),
  // 미저장 결과만 있으면(hasDraft) 확인 후 이동을 허용한다 — 허용되면 플래그도 같이 내린다.
  const tryLeaveCurrentScreen = () => {
    if (isAnalyzingRef.current) {
      window.alert("분석이 진행 중입니다. 완료된 뒤 이동해 주세요.");
      return false;
    }
    if (hasDraftRef.current && !window.confirm("저장하지 않은 분석 결과가 있습니다. 버리고 이동할까요?")) {
      return false;
    }
    hasDraftRef.current = false;
    return true;
  };

  const guardedSetView = (next: View) => {
    if (next === view) return; // 이미 그 탭 — 아무것도 unmount 되지 않으므로 물어볼 필요 없음
    if (!tryLeaveCurrentScreen()) return;
    setView(next);
  };

  const openCustomer = (id: string) => {
    if (view !== "customers" && !tryLeaveCurrentScreen()) return;
    setFocusCustomer((f) => ({ id, nonce: (f?.nonce ?? 0) + 1 }));
    setView("customers");
  };
  const openExpiryView = () => {
    if (view !== "customers" && !tryLeaveCurrentScreen()) return;
    setFocusListView((f) => ({ view: "expiry", nonce: (f?.nonce ?? 0) + 1 }));
    setView("customers");
    setBannerDismissed(true);
  };
  const doLogout = () => {
    logout();
    setAuth("out");
  };

  const tab = (key: View, label: string) => (
    <button
      onClick={() => guardedSetView(key)}
      style={{
        padding: "6px 14px",
        border: "none",
        borderBottom: view === key ? "2px solid #2563eb" : "2px solid transparent",
        background: "none",
        fontWeight: view === key ? 600 : 400,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );

  return (
    <div>
      {(offline || licenseErr) && (
        <div style={{ background: licenseErr ? "#fde8e8" : "#fff7e6", color: "#8a4b00", fontSize: 12, padding: "4px 12px" }}>
          {licenseErr ?? "오프라인 모드 — 서버에 연결되면 라이선스가 갱신됩니다."}
        </div>
      )}
      <nav style={{ display: "flex", gap: 4, borderBottom: "1px solid #ddd", padding: "8px 12px 0", alignItems: "center", position: "sticky", top: 0, background: "#fff", zIndex: 20 }}>
        {tab("home", "홈")}
        {tab("newcustomer", "새 고객")}
        {tab("customers", "고객 목록")}
        {tab("assistant", "AI 문의")}
        {tab("diagnostics", "설정·진단")}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#888" }}>
          {getUser()?.email}{" "}
          <button onClick={doLogout} style={{ fontSize: 12 }}>
            로그아웃
          </button>
        </span>
      </nav>
      {expiringCount > 0 && !bannerDismissed && (
        <div style={{ background: "#fff3e0", color: "#8a4b00", fontSize: 13, padding: "6px 12px", display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid #f0d9b5" }}>
          <span
            onClick={openExpiryView}
            style={{ cursor: "pointer", textDecoration: "underline" }}
          >
            만기 임박 계약 {expiringCount}건 — 고객 목록에서 확인
          </span>
          <button
            onClick={() => setBannerDismissed(true)}
            style={{ marginLeft: "auto", fontSize: 12, border: "none", background: "none", cursor: "pointer", color: "#8a4b00" }}
          >
            ✕
          </button>
        </div>
      )}
      {view === "home" && <HomeScreen onOpenCustomer={openCustomer} />}
      {view === "newcustomer" && (
        <CustomersScreen
          screen="new"
          onOpenCustomer={openCustomer}
          onDraftChange={handleDraftChange}
        />
      )}
      {view === "customers" && (
        <CustomersScreen
          screen="list"
          focusCustomer={focusCustomer}
          focusListView={focusListView}
          onOpenCustomer={openCustomer}
          onFocusConsumed={() => setFocusCustomer(null)}
          onListViewConsumed={() => setFocusListView(null)}
          onDraftChange={handleDraftChange}
        />
      )}
      {view === "assistant" && <AssistantScreen onOpenCustomer={openCustomer} />}
      {view === "diagnostics" && <DiagnosticsScreen onOpenCustomer={openCustomer} />}
    </div>
  );
}

export default App;
