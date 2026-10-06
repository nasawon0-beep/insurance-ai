import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import LoginScreen from "./LoginScreen";
import CustomersScreen from "./Customers";
import AssistantScreen from "./Assistant";
import CoverageAnalysisScreen from "./CoverageAnalysis";
import { AssistantProvider, useAssistant } from "./AssistantContext";
import { CoverageTable, PolicyList, savePolicies, type PolicyDraft } from "./PolicyList";
import { StatCard } from "./components/DashboardCards";
import OnboardingScreen from "./components/OnboardingScreen";
import { ThemeToggle, ThemeSelector } from "./components/ThemeToggle";
import {
  changePassword,
  fetchMe,
  getToken,
  getUser,
  listDevices,
  loadEngineSettings,
  logout,
  removeDevice,
  resolveLicense,
  rrnEnabled,
} from "./auth";
import { track } from "./usage";
import ImportWizard from "./ImportWizard";
import { invoke } from "@tauri-apps/api/core";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { engineFetch, ensureLocalEngineRecovered } from "./engine";
import { formatLocalEngineNetworkError, isFetchNetworkError } from "./localEngineErrors";
import { checkForUpdate, installUpdate, type UpdateCheck } from "./updater";

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

// LoginScreen is now imported from ./LoginScreen.tsx

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
    <div style={{ fontSize: 12, color: "#8a6500", background: "var(--color-bg-surface)", padding: 8, marginBottom: 12 }}>
      {ok
        ? "⚠ AI 분석 기능이 지금 안 됩니다. (고객 관리는 정상 작동합니다.)"
        : "⚠ 일부 기능이 일시적으로 멈췄습니다. 앱을 껐다 다시 켜 주세요."}
    </div>
  );
}

function Card({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div style={{ border: "1px solid var(--color-border-default)", borderRadius: 8, padding: 12, marginBottom: 14 }}>
      <h3 style={{ margin: "0 0 8px", fontSize: "1.125rem" }}>
        {title} <span style={{ color: "var(--color-text-secondary)", fontWeight: 400 }}>({count})</span>
      </h3>
      {count === 0 ? <p style={{ color: "var(--color-text-secondary)", margin: 0 }}>없음</p> : children}
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

type BackgroundJobStatus = "running" | "done" | "error";
type BackgroundJobEvent = { id: string; label: string; status: BackgroundJobStatus; message: string };
type BackgroundJobReporter = (event: BackgroundJobEvent) => void;

function CapturePanel({
  onOpenCustomer,
  onChanged,
  onBackgroundJob,
  title = "던져넣기",
  subtitle = "한 명이든 여러 명이든, 녹취·PDF·텍스트 무엇이든",
  jobLabel = "던져넣기",
  accept = "audio/*,.m4a,.mp3,.wav,.aac,.aiff,.pdf,.txt,.csv,.md,text/plain",
  defaultContext = "",
  contextPlaceholder = "이 파일에 대해 알려주세요 — 누구 자료인지(고객 이름), 어느 보험사, 어떤 문서(제안서·증권·보장분석·녹취)인지, 특별히 봐야 할 점.\n여기 적은 내용이 분석에 함께 들어갑니다. (저장은 안 됩니다)",
  textPlaceholder = "고객 정보를 붙여넣으세요. 여러 명이면 한 줄에 한 명씩.\n또는 녹취·PDF·텍스트 파일을 위 영역에 끌어다 놓으세요.",
}: {
  onOpenCustomer: (id: string) => void;
  onChanged: () => void;
  onBackgroundJob?: BackgroundJobReporter;
  title?: string;
  subtitle?: string;
  jobLabel?: string;
  accept?: string;
  defaultContext?: string;
  contextPlaceholder?: string;
  textPlaceholder?: string;
}) {
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
    let r: Response;
    try {
      r = await engineFetch(path, {
        ...init,
        headers,
      });
    } catch (e) {
      if (isFetchNetworkError(e)) {
        throw new Error(formatLocalEngineNetworkError(e));
      }
      throw e;
    }
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
    onBackgroundJob?.({ id: "capture-analyze", label: jobLabel, status: "running", message: file ? `${jobLabel} 파일 분석 중…` : `${jobLabel} 텍스트 분석 중…` });
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
      onBackgroundJob?.({ id: "capture-analyze", label: jobLabel, status: "done", message: `${jobLabel} 분석 완료 — ${(b.items ?? []).length}건` });
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
      const message = e instanceof Error ? e.message : String(e);
      setErr(message);
      onBackgroundJob?.({ id: "capture-analyze", label: jobLabel, status: "error", message: `${jobLabel} 분석 실패: ${message}` });
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
    input.accept = accept;
    const f: File | null = await new Promise((resolve) => {
      input.onchange = () => {
        const selected = input.files?.[0] ?? null;
        input.value = "";
        resolve(selected);
      };
      input.click();
    });
    if (f) { setStaged(f); if (defaultContext) setContext(defaultContext); } // 바로 분석하지 않고 설명을 받는다
  };

  const patchRow = (i: number, p: Record<string, unknown>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));

  const recalculateCoverageIfNeeded = async (cid: string, row: any) => {
    if (row.doc_type !== "보장분석") return false;
    await api(`/customers/${cid}/coverage-analysis/recalculate`, {
      method: "POST",
      body: JSON.stringify({ audience: "customer", force: true, source: "capture_pdf" }),
    });
    return true;
  };

  const run = async () => {
    batchRef.current = crypto.randomUUID().replace(/-/g, "");
    setErr(null);
    setMsg(null);
    setBusy(true);
    setPhase("저장 중…");
    onBackgroundJob?.({ id: "capture-save", label: `${jobLabel} 저장`, status: "running", message: `${jobLabel} 저장 중…` });
    const res = { created: 0, merged: 0, skipped: 0, failed: 0, policies: 0, consultations: 0, coverageAnalyses: 0 };
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
        if (cid && (await recalculateCoverageIfNeeded(cid, row))) res.coverageAnalyses++;
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
      onBackgroundJob?.({ id: "capture-save", label: `${jobLabel} 저장`, status: "done", message: `${jobLabel} 저장 완료` });
      reset();
      onOpenCustomer((lastNewId || mergedId)!);
      return;
    }
    reset();
    setMsg(
      `완료 — 신규 ${res.created} · 갱신 ${res.merged} · 건너뜀 ${res.skipped}` +
        (res.policies ? ` · 보험계약 ${res.policies}` : "") +
        (res.coverageAnalyses ? ` · 보장분석 ${res.coverageAnalyses}건 자동 실행` : "") +
        (res.failed ? ` · 실패 ${res.failed}` : ""),
    );
    const errs = [
      saveErr && "저장 실패: " + saveErr,
      policyFails.length &&
        `계약 저장 실패 ${policyFails.length}건:\n` +
          policyFails.map((f) => `· ${f.label} — ${f.error}`).join("\n"),
      rrnErr && "주민번호 저장 실패: " + rrnErr,
    ].filter(Boolean);
    if (errs.length) {
      const message = errs.join(" / ");
      setErr(message);
      onBackgroundJob?.({ id: "capture-save", label: `${jobLabel} 저장`, status: "error", message: `${jobLabel} 저장 오류: ${message}` });
    } else {
      onBackgroundJob?.({ id: "capture-save", label: `${jobLabel} 저장`, status: "done", message: `${jobLabel} 완료 — 신규 ${res.created} · 갱신 ${res.merged}` });
    }
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
        if (f) { setStaged(f); if (defaultContext) setContext(defaultContext); } // 바로 분석하지 않고 설명을 받는다
      }}
      style={{
        border: dragOver ? "2px dashed var(--primary-500)" : "2px dashed var(--color-border-default)",
        borderRadius: 16,
        padding: 16,
        marginBottom: 16,
        background: dragOver 
          ? "linear-gradient(135deg, #1e3a5f 0%, #1a2942 100%)" 
          : "var(--color-bg-surface)",
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        boxShadow: dragOver 
          ? "0 4px 6px -1px rgba(59, 130, 246, 0.1), 0 2px 4px -1px rgba(59, 130, 246, 0.06)"
          : "var(--shadow-sm)",
      }}
    >
      {lastBatch && Date.now() - lastBatch.at < 30 * 60 * 1000 && (
        <div style={{ 
          border: "1px solid #a5b4fc", 
          borderRadius: 8, 
          padding: 10, 
          marginBottom: 12, 
          background: "var(--color-bg-surface)",
          fontSize: 13
        }}>
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
      <div style={{ 
        display: "flex", 
        alignItems: "center", 
        justifyContent: "space-between",
        gap: 12, 
        marginBottom: 12,
        paddingBottom: 10,
        borderBottom: dragOver ? "2px solid #3b82f6" : "1px solid #e5e5e5"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ 
            width: 40, 
            height: 40, 
            borderRadius: 10, 
            background: dragOver ? "var(--primary-500)" : "var(--neutral-100)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 20,
            transition: "all 0.3s"
          }}>
            {dragOver ? "📥" : "⚡"}
          </div>
          <div>
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: "var(--color-text-primary)" }}>
              {title} {dragOver && "— 여기에 놓으세요"}
            </h3>
            <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-secondary)" }}>
              {subtitle}
            </p>
          </div>
        </div>
        {rows.length > 0 && (
          <button
            aria-label="상단 분석 후 저장"
            onClick={run}
            disabled={busy}
            style={{
              padding: "8px 14px",
              background: busy ? "var(--color-bg-base)" : "var(--primary-500)",
              color: busy ? "#a3a3a3" : "#fff",
              border: "1px solid var(--color-border-default)",
              borderRadius: 8,
              cursor: busy ? "not-allowed" : "pointer",
              fontSize: 13,
              fontWeight: 600,
              whiteSpace: "nowrap"
            }}
          >
            {busy ? "처리 중…" : rows.length > 1 ? `${rows.length}명 저장` : "분석 후 저장"}
          </button>
        )}
      </div>
      {staged ? (
        <div
          style={{
            border: "2px solid var(--primary-500)",
            borderRadius: 12,
            padding: 12,
            background: "var(--color-bg-surface)",
            boxShadow: "0 1px 3px rgba(59, 130, 246, 0.1)"
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ 
              width: 32, 
              height: 32, 
              borderRadius: 8, 
              background: "var(--color-bg-base)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 16
            }}>
              📎
            </div>
            <span style={{ fontWeight: 600, color: "var(--color-text-primary)", fontSize: 14 }}>{staged.name}</span>
            <button 
              onClick={() => setStaged(null)} 
              disabled={busy} 
              style={{ 
                marginLeft: "auto",
                fontSize: 13,
                padding: "4px 10px",
                borderRadius: 6,
                border: "1px solid #e5e5e5",
                background: "var(--color-bg-surface)",
                cursor: "pointer",
                color: "#525252"
              }}
            >
              ✕ 파일 빼기
            </button>
          </div>
          <textarea
            style={{ 
              width: "100%", 
              boxSizing: "border-box", 
              minHeight: 70, 
              padding: 10,
              borderRadius: 8,
              border: "1px solid var(--color-border-default)",
              backgroundColor: "var(--color-bg-surface)",
              color: "var(--color-text-primary)",
              fontSize: 14,
              fontFamily: "inherit",
              resize: "vertical",
              outline: "none",
              transition: "border 0.2s"
            }}
            placeholder={contextPlaceholder}
            value={context}
            onChange={(e) => setContext(e.target.value)}
            autoFocus
            onFocus={(e) => e.target.style.borderColor = "#3b82f6"}
            onBlur={(e) => e.target.style.borderColor = "var(--color-border-default)"}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <button
              onClick={() => analyze(staged)}
              disabled={busy}
              style={{ 
                padding: "10px 18px",
                background: busy ? "var(--color-bg-base)" : "var(--primary-500)",
                color: busy ? "#a3a3a3" : "#fff",
                border: "1px solid var(--color-border-default)",
                borderRadius: 8,
                cursor: busy ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 600,
                transition: "all 0.15s"
              }}
            >
              {busy ? "⏳ 분석 중…" : "✨ 이 파일 분석"}
            </button>
            <button 
              onClick={() => { setStaged(null); setContext(""); }} 
              disabled={busy} 
              style={{
                padding: "10px 18px",
                background: "var(--color-bg-surface)",
                color: "#525252",
                border: "1px solid #e5e5e5",
                borderRadius: 8,
                cursor: busy ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 500,
                transition: "all 0.15s"
              }}
            >
              취소
            </button>
          </div>
        </div>
      ) : (
        <>
          <div style={{
            background: dragOver ? "rgba(59, 130, 246, 0.15)" : "var(--color-bg-base)",
            border: dragOver ? "2px dashed var(--primary-500)" : "2px dashed var(--color-border-default)",
            borderRadius: 12,
            padding: "24px 16px",
            textAlign: "center",
            transition: "all 0.3s",
            marginBottom: 10
          }}>
            <div style={{
              fontSize: dragOver ? 48 : 40,
              marginBottom: 8,
              transition: "all 0.3s",
              transform: dragOver ? "scale(1.1)" : "scale(1)"
            }}>
              {dragOver ? "📥" : "📄"}
            </div>
            <p style={{ 
              margin: "0 0 8px", 
              fontSize: 14, 
              color: dragOver ? "#2563eb" : "#525252",
              fontWeight: 500
            }}>
              {dragOver 
                ? "여기에 파일을 놓으세요" 
                : "파일을 이 영역에 끌어다 놓으세요"}
            </p>
            <p style={{ margin: 0, fontSize: 13, color: "#a3a3a3" }}>
              녹취 (m4a, mp3, wav) · PDF · 텍스트 지원
            </p>
          </div>
          
          <textarea
            style={{ 
              width: "100%", 
              boxSizing: "border-box", 
              minHeight: 64, 
              padding: 10,
              borderRadius: 8,
              border: "1px solid var(--color-border-default)",
              backgroundColor: "var(--color-bg-surface)",
              color: "var(--color-text-primary)",
              fontSize: 14,
              fontFamily: "inherit",
              resize: "vertical",
              outline: "none",
              transition: "border 0.2s",
              marginBottom: 10
            }}
            placeholder={textPlaceholder}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onFocus={(e) => e.target.style.borderColor = "#3b82f6"}
            onBlur={(e) => e.target.style.borderColor = "var(--color-border-default)"}
          />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button 
              onClick={() => analyze()} 
              disabled={busy}
              style={{
                padding: "10px 18px",
                background: busy ? "var(--color-bg-base)" : "var(--primary-500)",
                color: busy ? "#a3a3a3" : "#fff",
                border: "1px solid var(--color-border-default)",
                borderRadius: 8,
                cursor: busy ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 600,
                transition: "all 0.15s"
              }}
            >
              {busy ? "⏳ 분석 중…" : "✨ 분석"}
            </button>
            <button 
              onClick={pickFile} 
              disabled={busy}
              style={{
                padding: "10px 18px",
                backgroundColor: "var(--color-bg-surface)",
                color: "var(--color-text-primary)",
                border: "1px solid var(--color-border-default)",
                borderRadius: 8,
                cursor: busy ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 500,
                transition: "all 0.15s"
              }}
              onMouseEnter={(e) => {
                if (!busy) {
                  e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = "var(--color-bg-surface)";
              }}
            >
              {busy ? "…" : "📎 파일 선택"}
            </button>
            {rows.length > 0 && !busy && (
              <button 
                onClick={reset} 
                style={{ 
                  marginLeft: "auto",
                  padding: "10px 18px",
                  background: "transparent",
                  color: "#737373",
                  border: "1px solid var(--color-border-default)",
                  cursor: "pointer",
                  fontSize: 14,
                  fontWeight: 500
                }}
              >
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
              {row.action !== "skip" && row.coverage_review && (
                <div style={{ margin: "8px 0", padding: "8px 10px", background: "#fff7ed", border: "1px solid #fed7aa", borderRadius: 6, maxWidth: 640, fontSize: 12 }}>
                  <div style={{ fontWeight: 700, marginBottom: 4 }}>검수 화면 — 보장분석/계약별 담보 분리 저장</div>
                  <div>
                    계약별 담보: 계약 {row.coverage_review.policy_coverage_contract_count ?? 0}개 · 담보 {row.coverage_review.policy_coverage_item_count ?? 0}건 · 미분류 {row.coverage_review.policy_coverage_unclassified_count ?? 0}건 · policy_id 연결 실패는 저장 시 재검사
                  </div>
                  <div>
                    보장분석 진단표: {row.coverage_review.coverage_item_count ?? 0}개 항목 · 확인필요 {row.coverage_review.needs_review_item_count ?? 0}건 · 상세 페이지 {(row.coverage_review.detected_detail_pages ?? []).join(", ") || "-"}
                  </div>
                  {row.policy_coverages?.length > 0 && (
                    <details style={{ marginTop: 4 }}>
                      <summary>계약별 담보 후보 보기 ({row.policy_coverages.length}건)</summary>
                      <div style={{ maxHeight: 160, overflow: "auto", marginTop: 4 }}>
                        {row.policy_coverages.slice(0, 40).map((c: any, ci: number) => (
                          <div key={ci} style={{ borderTop: ci ? "1px solid #fed7aa" : undefined, padding: "3px 0" }}>
                            {c.insurer || "-"} / {c.product_name || "-"} / {c.rider_name || "-"} → {c.standard_name || "-"} / {c.amount_text || "-"} / p.{c.source_page || "-"}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                  <div style={{ color: "#9a3412", marginTop: 4 }}>[검수 완료 후 저장] 버튼을 누를 때만 저장됩니다. 자동 저장은 하지 않습니다.</div>
                </div>
              )}
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

function HomeScreen({
  onOpenCustomer,
  onGoDataAnalysis,
  onGoCustomers,
  onGoCoverage,
}: {
  onOpenCustomer: (id: string) => void;
  onGoDataAnalysis: () => void;
  onGoCustomers: () => void;
  onGoCoverage: () => void;
}) {
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
    fontWeight: 600,
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
      <h2 style={{ marginTop: 0, fontSize: "1.5rem" }}>홈</h2>
      <EngineStrip />
      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.875rem", margin: "8px 0" }} title="계약 수·만기 임박을 내가 직접 가입시킨 계약으로만">
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
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, margin: "12px 0 20px" }}>
        <button onClick={onGoDataAnalysis} style={{ padding: "12px 14px", fontWeight: 700 }}>자료 업로드</button>
        <button onClick={onGoCustomers} style={{ padding: "12px 14px", fontWeight: 700 }}>고객 목록</button>
        <button onClick={onGoCoverage} style={{ padding: "12px 14px", fontWeight: 700 }}>보장현황 보기</button>
      </div>
      {error && <p style={{ color: "#b00" }}>대시보드 오류: {error}</p>}
      {!data ? (
        <p style={{ color: "#888" }}>불러오는 중…</p>
      ) : (
        <>
          {/* 2x2 통계 카드 그리드 - 반응형 */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4" style={{ marginBottom: 24 }}>
            <StatCard
              title="고객 수"
              value={data.counts.customers}
              subtitle="전체 고객"
              icon="👥"
            />
            <StatCard
              title="보험계약"
              value={data.counts.active_policies}
              subtitle={`전체 ${data.counts.policies}건`}
              icon="📋"
            />
            <StatCard
              title="상담 건수"
              value={data.counts.consultations}
              subtitle="누적 상담"
              icon="💬"
            />
            <StatCard
              title="이번 달 신규"
              value={data.follow_ups.length + data.recent_consultations.slice(0, 5).length}
              subtitle="후속 연락 · 최근 상담"
              icon="📈"
            />
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
  border: "1px solid var(--color-border-default)",
  borderRadius: 8,
  padding: 12,
  marginBottom: 12,
  background: "var(--color-bg-surface)",
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

function DataAnalysisScreen({
  onOpenCustomer,
  onDraftChange,
  onBackgroundJob,
}: {
  onOpenCustomer: (id: string) => void;
  onDraftChange: (state: { hasDraft: boolean; isAnalyzing: boolean }) => void;
  onBackgroundJob: BackgroundJobReporter;
}) {
  const [section, setSection] = useState<"new" | "bulk" | "customerPdf" | "coveragePdf">("new");
  const sectionButton = (key: typeof section, label: string, desc: string) => (
    <button
      key={key}
      onClick={() => setSection(key)}
      style={{
        textAlign: "left",
        padding: 14,
        border: `1px solid ${section === key ? "#2563eb" : "var(--color-border-default)"}`,
        borderRadius: 10,
        background: section === key ? "#eff6ff" : "var(--color-bg-surface)",
        color: "var(--color-text-primary)",
        cursor: "pointer",
      }}
    >
      <div style={{ fontWeight: 800, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{desc}</div>
    </button>
  );

  return (
    <div style={{ maxWidth: 1040, margin: "24px auto", padding: "0 16px" }}>
      <h2 style={{ margin: "0 0 4px" }}>자료분석</h2>
      <p style={{ margin: "0 0 16px", color: "var(--color-text-secondary)", fontSize: 13 }}>
        업로드 전용 화면입니다. 조회는 고객 탭과 보장분석 탭에서 진행합니다.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10, marginBottom: 16 }}>
        {sectionButton("new", "새고객 등록", "상세 입력·파일/텍스트 분석")}
        {sectionButton("bulk", "일괄등록", "엑셀/CSV 업로드")}
        {sectionButton("customerPdf", "고객자료 PDF", "증권·제안서·상담자료 업로드")}
        {sectionButton("coveragePdf", "보장분석서 PDF", "보장분석 자료 업로드")}
      </div>
      <div style={{ display: section === "new" ? "block" : "none" }} aria-hidden={section !== "new"}>
        <CustomersScreen
          screen="new"
          onOpenCustomer={onOpenCustomer}
          onDraftChange={onDraftChange}
          onBackgroundJob={onBackgroundJob}
        />
      </div>
      <div style={{ display: section === "bulk" ? "block" : "none" }} aria-hidden={section !== "bulk"}>
        <div style={pboxStyle}>
          <b>일괄등록 (엑셀)</b>
          <ImportWizard onBackgroundJob={onBackgroundJob} />
        </div>
      </div>
      <div style={{ display: section === "customerPdf" ? "block" : "none" }} aria-hidden={section !== "customerPdf"}>
        <CapturePanel
          title="고객자료 PDF"
          subtitle="고객자료·증권·제안서 PDF를 업로드해 고객/계약/상담 정보를 저장합니다."
          jobLabel="고객자료 PDF"
          accept="application/pdf,.pdf"
          defaultContext="고객자료 PDF"
          contextPlaceholder="고객 이름, 보험사, 자료 종류(증권·제안서·상담자료), 특별히 봐야 할 점을 적어주세요."
          textPlaceholder="PDF를 선택하거나 끌어다 놓으세요."
          onOpenCustomer={onOpenCustomer}
          onChanged={() => undefined}
          onBackgroundJob={onBackgroundJob}
        />
      </div>
      <div style={{ display: section === "coveragePdf" ? "block" : "none" }} aria-hidden={section !== "coveragePdf"}>
        <CapturePanel
          title="보장분석서 PDF"
          subtitle="보장분석서 PDF를 업로드해 고객별 보장분석 데이터를 저장합니다."
          jobLabel="보장분석서 PDF"
          accept="application/pdf,.pdf"
          defaultContext="보장분석서 PDF / 보장분석"
          contextPlaceholder="고객 이름과 보장분석서라는 점을 적어주세요. 저장 시 해당 customer_id로 상담/보장분석이 연결됩니다."
          textPlaceholder="보장분석서 PDF를 선택하거나 끌어다 놓으세요."
          onOpenCustomer={onOpenCustomer}
          onChanged={() => undefined}
          onBackgroundJob={onBackgroundJob}
        />
      </div>
    </div>
  );
}

function DiagnosticsScreen({
  onOpenCustomer,
  onRestartOnboarding,
}: {
  onOpenCustomer: (id: string) => void;
  onRestartOnboarding: () => void;
}) {
  const [d, setD] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lic, setLic] = useState<Record<string, any> | null>(null);
  const [devices, setDevices] = useState<any[]>([]);
  const [recompBusy, setRecompBusy] = useState(false);
  const [recomp, setRecomp] = useState<Record<string, any> | null>(null);

  const [me, setMe] = useState<{ id: string; email: string; created_at?: string } | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordErr, setPasswordErr] = useState<string | null>(null);
  const [passwordToast, setPasswordToast] = useState<string | null>(null);
  const passwordDetails = useRef<HTMLDetailsElement>(null);
  const passwordToastTimer = useRef<number | null>(null);
  const [upd, setUpd] = useState<UpdateCheck | { kind: "checking" } | { kind: "installing" } | null>(null);

  const runUpdateCheck = async () => {
    setUpd({ kind: "checking" });
    setUpd(await checkForUpdate());
  };
  const runUpdateInstall = async (u: Extract<UpdateCheck, { kind: "available" }>) => {
    setUpd({ kind: "installing" });
    try {
      await installUpdate(u.update);
    } catch (e) {
      setUpd({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

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

  const box: React.CSSProperties = { 
    border: "1px solid var(--color-border-default)", 
    borderRadius: 8, 
    padding: 12, 
    marginBottom: 12,
    background: "var(--color-bg-surface)",
    boxShadow: "var(--shadow-sm)"
  };
  const kv = (k: string, v: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: "0.875rem", padding: "6px 0" }}>
      <span style={{ minWidth: 150, color: "var(--color-text-secondary)", fontWeight: 500 }}>{k}</span>
      <span style={{ color: "var(--color-text-primary)" }}>{v}</span>
    </div>
  );

  return (
    <div style={{ maxWidth: 760, margin: "24px auto", padding: "0 20px" }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: "0 0 4px", fontSize: "1.375rem", fontWeight: 600, color: "var(--color-text-primary)" }}>설정 · 진단</h2>
        <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--color-text-secondary)" }}>엔진 상태 확인 및 시스템 설정 관리</p>
      </div>
      
      {/* 테마 설정 */}
      <div style={{ 
        border: "1px solid var(--color-border-default)", 
        borderRadius: 8, 
        padding: 16, 
        marginBottom: 16,
        background: "var(--color-bg-surface)",
        boxShadow: "var(--shadow-sm)"
      }}>
        <h3 style={{ margin: "0 0 12px", fontSize: "1rem", fontWeight: 600, color: "var(--color-text-primary)" }}>테마</h3>
        <ThemeSelector />
      </div>
      
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <button
          onClick={() => void runUpdateCheck()}
          disabled={upd?.kind === "checking" || upd?.kind === "installing"}
        >
          {upd?.kind === "checking" ? "확인 중…" : "업데이트 확인"}
        </button>
        {upd?.kind === "latest" && <span style={{ color: "#176b2c", fontSize: "0.875rem" }}>최신 버전입니다</span>}
        {upd?.kind === "unsupported" && (
          <span style={{ color: "#888", fontSize: "0.875rem" }}>데스크톱 앱에서만 지원됩니다</span>
        )}
        {upd?.kind === "installing" && (
          <span style={{ color: "#888", fontSize: "0.875rem" }}>다운로드·설치 중… 창을 닫지 마세요</span>
        )}
        {upd?.kind === "error" && (
          <span style={{ color: "#b00", fontSize: "0.875rem" }}>업데이트 실패: {upd.message}</span>
        )}
        {upd?.kind === "available" && (
          <>
            <span style={{ fontSize: "0.875rem" }}>새 버전 {upd.version} 있음</span>
            <button onClick={() => void runUpdateInstall(upd)}>지금 설치하고 재시작</button>
          </>
        )}
      </div>
      {err && <p style={{ color: "#b00" }}>{err}</p>}
      {!d ? (
        <p style={{ color: "#888" }}>불러오는 중…</p>
      ) : (
        <>
          <div style={box}>
            <div style={{ marginBottom: 8 }}><b>엔진</b></div>
            {kv("Local Engine", d.engine?.local_engine ?? "-")}
            {kv("Ollama", d.engine?.ollama === "connected" ? "연결됨 ✅" : "연결 안됨 ❌")}
            {kv("모델", (d.engine?.models ?? []).join(", ") || "-")}
            <button onClick={onRestartOnboarding} style={{ marginTop: 8 }}>
              Ollama/AI 모델 초기 설정 다시 실행
            </button>
          </div>
          <div style={box}>
            <div style={{ marginBottom: 8 }}><b>고객 DB 암호화</b></div>
            {kv("방식", d.customer_db?.encryption)}
            {kv("키 보관", d.customer_db?.key_source)}
          </div>

          <div style={box}>
            <div style={{ marginBottom: 8 }}><b>음성 인식 (Whisper)</b></div>
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
            <div style={{ marginBottom: 8 }}><b>만기 관리</b></div>
            <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", margin: "4px 0" }}>
              전 계약의 만기일·납입종료일을 보험기간(없으면 memo)에서 다시 계산합니다. 직접 입력한
              만기는 건드리지 않습니다. 여러 번 눌러도 안전합니다.
            </p>
            <button onClick={recomputeExpiry} disabled={recompBusy} style={{ marginTop: 4 }}>
              {recompBusy ? "재계산 중…" : "만기 일괄 재계산"}
            </button>
            {recomp && (
              <div style={{ marginTop: 8, fontSize: "0.875rem" }}>
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
                    <table style={{ borderCollapse: "collapse", fontSize: "0.75rem", width: "100%" }}>
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
            <div style={{ marginBottom: 8 }}><b>데이터</b></div>
            {kv("고객", d.data?.customers)}
            {kv("보험계약", `${d.data?.active_policies}/${d.data?.policies}`)}
            {kv("상담 이력", d.data?.consultations)}
            {kv("색인된 약관", `${d.rag?.docs?.length ?? 0}개 문서 / ${d.rag?.total_chunks ?? 0} 조각`)}
          </div>

          <div style={box}>
            <div style={{ marginBottom: 8 }}><b>내 계정</b></div>
            {kv("이메일", me?.email ?? getUser()?.email ?? "-")}
            {kv("가입일", me?.created_at ? me.created_at.slice(0, 10) : "-")}
            <details ref={passwordDetails} style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: "0.875rem" }}>비밀번호 변경</summary>
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
                {passwordErr && <p style={{ fontSize: "0.75rem", color: "#b00", margin: "6px 0 0" }}>{passwordErr}</p>}
              </form>
            </details>
          </div>

          <div style={box}>
            <div style={{ marginBottom: 8 }}><b>라이선스 · 기기</b></div>
            {lic ? (
              <>
                {kv("플랜", `${lic.plan}${lic.offline ? " (오프라인 확인)" : ""}`)}
                {kv("상태", lic.status === "expired" ? "만료됨 ❌" : "활성 ✅")}
                {kv("만료일", lic.expiry ?? "무기한")}
                {kv("등록 기기", `${devices.length} / ${lic.device_limit}대`)}
                {devices.map((v) => (
                  <div key={v.id} style={{ fontSize: "0.75rem", paddingLeft: 130 }}>
                    · {v.name || v.device_id.slice(0, 8)} ({v.app_version || "?"}){" "}
                    <button style={{ fontSize: "0.6875rem" }} onClick={async () => { await removeDevice(v.id); load(); }}>
                      해제
                    </button>
                  </div>
                ))}
              </>
            ) : (
              kv("상태", "control-server 연결 안 됨")
            )}
          </div>

          <h3 style={{ margin: "20px 0 8px", fontSize: "1rem" }}>데이터 관리</h3>
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
type View = "home" | "customers" | "data" | "coverage" | "assistant" | "diagnostics";

function AssistantTabButton({ view, setView }: { view: View; setView: (v: View) => void }) {
  const { pendingTasks } = useAssistant();
  
  return (
    <button
      onClick={() => setView("assistant")}
      style={{
        padding: "6px 14px",
        border: "1px solid var(--color-border-default)",
        borderBottom: view === "assistant" ? "2px solid #2563eb" : "2px solid transparent",
        background: "none",
        fontWeight: view === "assistant" ? 600 : 400,
        cursor: "pointer",
        position: "relative",
      }}
    >
      AI 문의
      {pendingTasks > 0 && (
        <span
          style={{
            position: "absolute",
            top: 4,
            right: 4,
            background: "#ef4444",
            color: "#fff",
            fontSize: 10,
            fontWeight: 600,
            borderRadius: "50%",
            width: 16,
            height: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {pendingTasks}
        </span>
      )}
    </button>
  );
}

function AssistantBackgroundBanner() {
  const { pendingTasks } = useAssistant();
  if (pendingTasks <= 0) return null;
  return (
    <div style={{ background: "#f5f3ff", color: "#5b21b6", fontSize: 13, padding: "8px 12px", borderBottom: "1px solid #ddd6fe" }} role="status" aria-live="polite">
      AI문의 진행 중… 다른 탭에서도 계속 처리됩니다.
    </div>
  );
}

function App() {
  const [auth, setAuth] = useState<"checking" | "in" | "out">("checking");
  const [needsOnboarding, setNeedsOnboarding] = useState(
    () => localStorage.getItem("iai.ollamaOnboardingDone") !== "1" && localStorage.getItem("iai.ollamaOnboardingSkipped") !== "1",
  );
  const [offline, setOffline] = useState(false);
  const [licenseErr, setLicenseErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("home");
  // nonce 를 매번 바꿔서, 같은 고객을 연속으로 열어도 목록 화면이 반응하게 한다
  const [focusCustomer, setFocusCustomer] = useState<{ id: string; nonce: number } | null>(null);
  const [focusListView, setFocusListView] = useState<{ view: "expiry" | "birthday"; nonce: number } | null>(null);
  const [expiringCount, setExpiringCount] = useState(0);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [showUpdateDialog, setShowUpdateDialog] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState<Extract<UpdateCheck, { kind: "available" }> | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [engineReconnect, setEngineReconnect] = useState<{ status: "idle" | "connecting" | "error"; error?: string }>({ status: "idle" });
  const [engineNotice, setEngineNotice] = useState<{ status: "idle" | "recovering" | "recovered" | "error"; message?: string }>({ status: "idle" });
  const [backgroundJobs, setBackgroundJobs] = useState<BackgroundJobEvent[]>([]);
  const [toast, setToast] = useState<BackgroundJobEvent | null>(null);
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

  const reportBackgroundJob = useCallback((event: BackgroundJobEvent) => {
    setBackgroundJobs((prev) => {
      const without = prev.filter((job) => job.id !== event.id);
      return event.status === "running" ? [...without, event] : without;
    });
    if (event.status !== "running") {
      setToast(event);
      window.setTimeout(() => {
        setToast((current) => (current?.id === event.id && current?.message === event.message ? null : current));
      }, 6000);
    }
  }, []);

  const checkForUpdates = useCallback(async () => {
    try {
      const result = await checkForUpdate();
      if (result.kind === "available") {
        setUpdateAvailable(result);
        setShowUpdateDialog(true);
      }
    } catch (error) {
      console.error("업데이트 확인 실패:", error);
    }
  }, []);

  const downloadAndInstall = useCallback(async () => {
    if (!updateAvailable) return;

    setDownloading(true);
    setDownloadProgress(0);

    try {
      await installUpdate(updateAvailable.update, (event: any) => {
        switch (event.event) {
          case "Started":
            setDownloadProgress(0);
            break;
          case "Progress": {
            const downloaded = Number(event.data?.downloaded ?? 0);
            const contentLength = Number(event.data?.contentLength ?? 0);
            const percent = contentLength > 0 ? Math.round((downloaded / contentLength) * 100) : 0;
            setDownloadProgress(Math.max(0, Math.min(100, percent)));
            break;
          }
          case "Finished":
            setDownloadProgress(100);
            break;
        }
      });
    } catch (error) {
      console.error("업데이트 실패:", error);
      alert("업데이트 설치 실패: " + (error instanceof Error ? error.message : String(error)));
      setDownloading(false);
    }
  }, [updateAvailable]);

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

  const waitForEngineHealth = useCallback(async () => {
    let lastError = "local-engine 연결 확인 실패";
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const response = await engineFetch("/health");
        if (response.ok) {
          const health = await response.json();
          if (health?.local_engine === "ok") return;
          lastError = `local-engine 상태: ${health?.local_engine ?? "unknown"}`;
        } else {
          lastError = `local-engine health HTTP ${response.status}`;
        }
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    throw new Error(lastError);
  }, []);

  const recoverLocalEngine = useCallback(async (showRecovered = true) => {
    setEngineNotice({ status: "recovering", message: "로컬 엔진 복구 중..." });
    try {
      await ensureLocalEngineRecovered();
      await waitForEngineHealth();
      void loadEngineSettings();
      if (showRecovered) {
        setEngineNotice({ status: "recovered", message: "로컬 엔진 재시작 완료. 다시 시도하세요." });
        window.setTimeout(() => setEngineNotice((current) => (current.status === "recovered" ? { status: "idle" } : current)), 6000);
      } else {
        setEngineNotice({ status: "idle" });
      }
    } catch (error) {
      try {
        await waitForEngineHealth();
        void loadEngineSettings();
        setEngineNotice(showRecovered ? { status: "recovered", message: "로컬 엔진 연결이 복구되었습니다. 다시 시도하세요." } : { status: "idle" });
        if (showRecovered) {
          window.setTimeout(() => setEngineNotice((current) => (current.status === "recovered" ? { status: "idle" } : current)), 6000);
        }
        return;
      } catch {
        // 원래 복구 실패 원인을 사용자에게 보여준다.
      }
      setEngineNotice({
        status: "error",
        message: `로컬 엔진 복구 실패. 실행 중인 local-engine.exe를 종료한 뒤 앱을 다시 시작해 주세요. (${error instanceof Error ? error.message : String(error)})`,
      });
    }
  }, [waitForEngineHealth]);

  const finishOnboardingAndConnectEngine = useCallback(async () => {
    setNeedsOnboarding(false);
    setEngineReconnect({ status: "connecting" });
    try {
      await invoke("ensure_local_engine");
      await waitForEngineHealth();
      void loadEngineSettings();
      setEngineReconnect({ status: "idle" });
    } catch (error) {
      setEngineReconnect({
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [waitForEngineHealth]);

  const relaunchApp = useCallback(async () => {
    const { relaunch } = await import("@tauri-apps/plugin-process");
    await relaunch();
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  useEffect(() => {
    if (auth !== "in" || needsOnboarding) return;
    void recoverLocalEngine(false);
  }, [auth, needsOnboarding, recoverLocalEngine]);

  useEffect(() => {
    // 앱 실행(프로세스) 당 1회만. 앱 시작 3초 뒤 백그라운드에서 자동 확인한다.
    // 실제 manifest/서명 검증은 패키지된 Tauri 앱에서만 동작할 수 있다.
    if (updateCheckStartedRef.current) return;
    updateCheckStartedRef.current = true;
    const timer = window.setTimeout(() => {
      void checkForUpdates();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [checkForUpdates]);

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
  if (needsOnboarding) {
    return <OnboardingScreen onComplete={() => void finishOnboardingAndConnectEngine()} onOllamaInstalled={() => undefined} />;
  }
  if (engineReconnect.status !== "idle") {
    return (
      <div style={{ maxWidth: 520, margin: "80px auto", padding: 24, textAlign: "center" }}>
        {engineReconnect.status === "connecting" ? (
          <>
            <h2>엔진 연결 중…</h2>
            <p style={{ color: "#666" }}>초기 설정을 반영하고 local-engine 연결을 확인하고 있습니다.</p>
          </>
        ) : (
          <>
            <h2>local-engine 연결 실패</h2>
            <p style={{ color: "#b00" }}>{engineReconnect.error ?? "엔진 연결을 확인할 수 없습니다."}</p>
            <p style={{ color: "#666" }}>다시 연결을 먼저 시도해 주세요. 계속 실패하면 앱 재시작을 실행할 수 있습니다.</p>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button onClick={() => void finishOnboardingAndConnectEngine()}>다시 연결</button>
              <button onClick={() => void relaunchApp()}>앱 재시작</button>
            </div>
          </>
        )}
      </div>
    );
  }

  // 현재 화면(주로 "새 고객"의 던져넣기·보장분석 결과)을 떠나도 되는지 확인.
  // 분석이 실제로 도는 중이면(isAnalyzing) 이동 자체를 막고(내부 목록 가드와 동일 정책),
  // 미저장 결과만 있으면(hasDraft) 확인 후 이동을 허용한다 — 허용되면 플래그도 같이 내린다.
  const tryLeaveCurrentScreen = () => {
    // 분석/저장 작업은 화면 컴포넌트를 숨긴 상태로 계속 살려두므로 탭 이동을 막지 않는다.
    // 미저장 결과(hasDraft)만 기존처럼 확인한다.
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

  const tab = (key: View, label: string) => {
    // AI 문의 탭은 별도 컴포넌트 사용 (배지 표시)
    if (key === "assistant") {
      return <AssistantTabButton key={key} view={view} setView={(v) => guardedSetView(v)} />;
    }
    
    return (
      <button
        key={key}
        onClick={() => guardedSetView(key)}
        style={{
          padding: "8px 16px",
          border: "none",
          borderBottom: view === key ? "2px solid #3b82f6" : "2px solid transparent",
          background: "none",
          fontWeight: view === key ? 700 : 500,
          fontSize: 14,
          color: view === key ? "#3b82f6" : "var(--color-text-primary)",
          cursor: "pointer",
          letterSpacing: "0.01em",
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <AssistantProvider>
      <div>
        {showUpdateDialog && updateAvailable && (
          <div className="update-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title">
            <div className="update-dialog">
              <h2 id="update-dialog-title">🎉 새 버전 출시!</h2>
              <p className="version">
                v{updateAvailable.update.currentVersion} → v{updateAvailable.version}
              </p>
              <p className="message">{updateAvailable.notes || "새로운 기능과 개선 사항이 추가되었습니다."}</p>

              {downloading ? (
                <div className="download-progress" role="status" aria-live="polite">
                  <div className="progress-bar" aria-hidden="true">
                    <div className="progress-fill" style={{ width: `${downloadProgress}%` }} />
                  </div>
                  <p>{downloadProgress}% 다운로드 중...</p>
                </div>
              ) : (
                <div className="actions">
                  <button onClick={downloadAndInstall} className="primary">
                    지금 업데이트
                  </button>
                  <button onClick={() => setShowUpdateDialog(false)} className="secondary">
                    나중에
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
        {(offline || licenseErr) && (
          <div style={{ background: licenseErr ? "#fde8e8" : "#fff7e6", color: "#8a4b00", fontSize: 12, padding: "4px 12px" }}>
            {licenseErr ?? "오프라인 모드 — 서버에 연결되면 라이선스가 갱신됩니다."}
          </div>
        )}
        {engineNotice.status !== "idle" && (
          <div style={{ background: engineNotice.status === "error" ? "#fef2f2" : "#eff6ff", color: engineNotice.status === "error" ? "#991b1b" : "#1d4ed8", fontSize: 13, padding: "8px 12px", display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid #bfdbfe" }} role="status" aria-live="polite">
            <span>{engineNotice.message ?? (engineNotice.status === "recovering" ? "로컬 엔진 복구 중..." : "로컬 엔진 상태를 확인했습니다.")}</span>
            <button onClick={() => void recoverLocalEngine()} disabled={engineNotice.status === "recovering"} style={{ marginLeft: "auto", fontSize: 12 }}>
              로컬 엔진 재시작
            </button>
            {engineNotice.status === "error" && <button onClick={() => void relaunchApp()} style={{ fontSize: 12 }}>앱 재시작</button>}
          </div>
        )}
        <nav style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--color-border-default)", padding: "8px 12px 0", alignItems: "center", position: "sticky", top: 0, background: "var(--color-bg-surface)", zIndex: 20 }}>
          {tab("home", "홈")}
          {tab("customers", "고객")}
          {tab("data", "자료분석")}
          {tab("coverage", "보장분석")}
          {tab("assistant", "AI 문의")}
          {tab("diagnostics", "설정·진단")}
          <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--color-text-secondary)", display: "flex", alignItems: "center", gap: 8 }}>
            <ThemeToggle />
            {getUser()?.email}{" "}
            <button onClick={doLogout} style={{ fontSize: 12 }}>
              로그아웃
            </button>
          </span>
        </nav>
        <AssistantBackgroundBanner />
        {backgroundJobs.length > 0 && (
          <div style={{ background: "#eff6ff", color: "#1d4ed8", fontSize: 13, padding: "8px 12px", borderBottom: "1px solid #bfdbfe" }} role="status" aria-live="polite">
            {backgroundJobs.map((job) => job.message).join(" · ")}
            <div style={{ height: 3, background: "#bfdbfe", borderRadius: 999, marginTop: 6, overflow: "hidden" }}>
              <div style={{ width: "35%", height: "100%", background: "#2563eb", borderRadius: 999 }} />
            </div>
          </div>
        )}
        {toast && (
          <div style={{ position: "fixed", right: 16, top: 64, zIndex: 1000, maxWidth: 360, background: toast.status === "error" ? "#fef2f2" : "#ecfdf5", color: toast.status === "error" ? "#991b1b" : "#166534", border: `1px solid ${toast.status === "error" ? "#fecaca" : "#bbf7d0"}`, borderRadius: 10, padding: "10px 14px", boxShadow: "0 8px 24px rgba(0,0,0,0.12)", fontSize: 13 }}>
            {toast.message}
          </div>
        )}
        {expiringCount > 0 && !bannerDismissed && (
          <div style={{ background: "var(--color-bg-surface)", color: "#8a4b00", fontSize: 13, padding: "6px 12px", display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid #f0d9b5" }}>
            <span
              onClick={openExpiryView}
              style={{ cursor: "pointer", textDecoration: "underline" }}
            >
              만기 임박 계약 {expiringCount}건 — 고객 목록에서 확인
            </span>
            <button
              onClick={() => setBannerDismissed(true)}
              style={{ marginLeft: "auto", fontSize: 12, border: "1px solid var(--color-border-default)", background: "none", cursor: "pointer", color: "#8a4b00" }}
            >
              ✕
            </button>
          </div>
        )}
        <div style={{ display: view === "home" ? "block" : "none" }} aria-hidden={view !== "home"}>
          <HomeScreen
            onOpenCustomer={openCustomer}
            onGoDataAnalysis={() => guardedSetView("data")}
            onGoCustomers={() => guardedSetView("customers")}
            onGoCoverage={() => guardedSetView("coverage")}
          />
        </div>
        <div style={{ display: view === "customers" ? "block" : "none" }} aria-hidden={view !== "customers"}>
          <CustomersScreen
            screen="list"
            focusCustomer={focusCustomer}
            focusListView={focusListView}
            onOpenCustomer={openCustomer}
            onFocusConsumed={() => setFocusCustomer(null)}
            onListViewConsumed={() => setFocusListView(null)}
            onDraftChange={handleDraftChange}
            onBackgroundJob={reportBackgroundJob}
          />
        </div>
        <div style={{ display: view === "data" ? "block" : "none" }} aria-hidden={view !== "data"}>
          <DataAnalysisScreen
            onOpenCustomer={openCustomer}
            onDraftChange={handleDraftChange}
            onBackgroundJob={reportBackgroundJob}
          />
        </div>
        <div style={{ display: view === "coverage" ? "block" : "none" }} aria-hidden={view !== "coverage"}>
          <CoverageAnalysisScreen />
        </div>
        <div style={{ display: view === "assistant" ? "block" : "none" }} aria-hidden={view !== "assistant"}>
          <AssistantScreen onOpenCustomer={openCustomer} />
        </div>
        <div style={{ display: view === "diagnostics" ? "block" : "none" }} aria-hidden={view !== "diagnostics"}>
          <DiagnosticsScreen
            onOpenCustomer={openCustomer}
            onRestartOnboarding={() => {
              localStorage.removeItem("iai.ollamaOnboardingDone");
              localStorage.removeItem("iai.ollamaOnboardingSkipped");
              localStorage.removeItem("iai.modelOnboardingSkipped");
              setNeedsOnboarding(true);
            }}
          />
        </div>
      </div>
    </AssistantProvider>
  );
}

export default App;
