import { useEffect, useRef, useState } from "react";
import { engineFetch } from "../engine";
import { invoke } from "@tauri-apps/api/core";
import "./MobileConnectionPanel.css";

type Props = { visible?: boolean; fetcher?: typeof engineFetch; onReset?: () => void };
export default function BusinessResetPanel({ visible = true, fetcher = engineFetch, onReset = () => window.location.reload() }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const requestId = useRef<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [held, setHeld] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!visible) { if (!pending.current) dialog.current?.close(); return; }
    let disposed = false;
    void fetcher("/business/reset/status").then(async r => {
      if (!r.ok) throw new Error("status unavailable");
      const value = await r.json(); if (!disposed) { setHeld(value.sync_paused === true); setError(null); }
    }).catch(() => { if (!disposed) setError("초기화 상태를 확인하지 못했습니다. 엔진 연결 상태를 확인해 주세요."); });
    return () => { disposed = true; };
  }, [visible, fetcher]);
  const open = () => { setConfirmation(""); setError(null); dialog.current?.showModal(); cancelButton.current?.focus(); };
  const submit = async () => {
    if (pending.current || confirmation !== "초기화") return;
    pending.current = true; setBusy(true); setError(null);
    try {
      requestId.current ??= crypto.randomUUID().replace(/-/g, "");
      const stopped = await invoke<{ running: boolean }>("mobile_sync_stop");
      if (stopped.running !== false) throw new Error("휴대폰 연결을 안전하게 중지하지 못했습니다. 초기화하지 않았습니다.");
      const response = await fetcher("/business/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation, confirmed: true, request_id: requestId.current }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "초기화 요청을 처리하지 못했습니다.");
      if (result.sync_paused !== true || result.request_id !== requestId.current) throw new Error("초기화 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.");
      setHeld(true); dialog.current?.close(); onReset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "초기화 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.");
    } finally { pending.current = false; setBusy(false); }
  };
  return <section className="mobile-connection" aria-label="위험 작업" style={{ border: "1px solid var(--color-border-default)", padding: 16, borderRadius: 8, margin: "20px 0", color: "var(--color-text-primary)", fontSize: "1rem", lineHeight: 1.7 }}>
    <b>위험 작업</b>
    <p>PC에 저장된 고객·계약·상담·분석결과 등 업무 자료를 지웁니다.</p>
    <button disabled={busy} onClick={open}>전체 데이터 초기화</button>
    {held && <p role="status">초기화 후 휴대폰 동기화가 중지되어 있습니다. 휴대폰 자료와 등록은 유지되며 자동으로 재개하지 않습니다.</p>}
    <dialog ref={dialog} className="mc-dialog" aria-labelledby="business-reset-title" onCancel={event => { if (pending.current) event.preventDefault(); }}>
      <h3 id="business-reset-title">전체 데이터 초기화</h3>
      <p>삭제: 이 PC의 고객·계약·상담·분석결과와 관련 동기화 자료.</p>
      <p>유지: 로그인·이용권·등록 휴대폰·기기 인증·암호화 키·원본 PDF·백업·보안 감사 기록.</p>
      <p className="mc-attention">휴대폰 자료는 지우지 않습니다. 새 PC 업무공간은 PC만 사용하며, 휴대폰 동기화 재개는 백업 영역에서 초기화 전 업무를 복원한 뒤 선택할 수 있습니다.</p>
      <p>암호화된 안전 백업을 만든 뒤 처리합니다. 초기화 후 화면을 새로 불러옵니다.</p>
      <label>계속하려면 ‘초기화’를 정확히 입력하세요.
        <input aria-label="초기화 확인 입력" value={confirmation} disabled={busy} autoComplete="off" onChange={e => setConfirmation(e.currentTarget.value)} style={{ display: "block", width: "100%", boxSizing: "border-box", font: "inherit", margin: "8px 0" }} />
      </label>
      <div className="mc-choices">
        <button ref={cancelButton} autoFocus disabled={busy} onClick={() => dialog.current?.close()}>취소</button>
        <button disabled={busy || confirmation !== "초기화"} onClick={() => void submit()} style={{ color: "var(--color-text-primary)", border: "2px solid var(--color-error, #b42318)" }}>{busy ? "초기화 처리 중…" : "업무 데이터 지우기"}</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </dialog>
    {error && !dialog.current?.open && <p role="alert">{error}</p>}
  </section>;
}
