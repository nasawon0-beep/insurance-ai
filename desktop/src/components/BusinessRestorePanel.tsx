import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { engineFetch } from "../engine";
import "./MobileConnectionPanel.css";

type Props = { fetcher?: typeof engineFetch; onRestored?: () => void; onHoldChange?: (held: boolean) => void };
export default function BusinessRestorePanel({ fetcher = engineFetch, onRestored = () => window.location.reload(), onHoldChange }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const pending = useRef(false);
  const requestId = useRef<string | null>(null);
  const [held, setHeld] = useState(false);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [safety, setSafety] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [pcOnly, setPcOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    void fetcher("/business/reset/status").then(async r => {
      if (!r.ok) throw new Error("unavailable");
      const value = await r.json();
      if (typeof value.sync_paused !== "boolean") throw new Error("unknown");
      if (!disposed) {
        setHeld(value.sync_paused); onHoldChange?.(value.sync_paused);
        setCandidate(typeof value.restore_candidate === "string" ? value.restore_candidate : null);
        setSafety(typeof value.last_restore_safety_copy === "string" ? value.last_restore_safety_copy : null);
      }
    }).catch(() => { if (!disposed) { onHoldChange?.(true); setError("초기화 복원 상태를 확인하지 못했습니다. 연결 상태를 확인하고 화면을 다시 열어 주세요."); } });
    return () => { disposed = true; };
  }, [fetcher, onHoldChange]);
  const submit = async () => {
    if (pending.current || confirmation !== "복원" || !candidate) return;
    pending.current = true; setBusy(true); setError(null);
    try {
      requestId.current ??= crypto.randomUUID().replace(/-/g, "");
      const stopped = await invoke<{ running: boolean }>("mobile_sync_stop");
      if (stopped.running !== false) throw new Error("휴대폰 연결을 안전하게 중지하지 못했습니다. 복원하지 않았습니다.");
      const response = await fetcher("/business/reset/restore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation, confirmed: true, request_id: requestId.current, backup_filename: candidate }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "복원 요청을 처리하지 못했습니다.");
      if (result.restored !== true || result.request_id !== requestId.current || result.backup_filename !== candidate || typeof result.sync_paused !== "boolean" || typeof result.safety_copy !== "string") throw new Error("복원 결과를 확인하지 못했습니다. 같은 요청으로 다시 확인해 주세요.");
      dialog.current?.close(); onRestored();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "복원 결과를 확인하지 못했습니다."); }
    finally { pending.current = false; setBusy(false); }
  };
  if (!held && !error && !safety) return null;
  return <section id="business-reset-recovery" className="mobile-connection" aria-label="초기화 복원" style={{ fontSize: "1rem", color: "var(--color-text-primary)", lineHeight: 1.7, overflowWrap: "anywhere" }}>
    {held && <>
      <p>초기화 후 PC만 사용 중입니다. 휴대폰 자료와 등록은 유지됩니다.</p>
      <div className="mc-choices">
        <button disabled={busy} onClick={() => setPcOnly(true)}>PC만 계속 사용</button>
        <button disabled={busy || !candidate} onClick={() => { setConfirmation(""); setError(null); dialog.current?.showModal(); cancel.current?.focus(); }} className="mc-primary">초기화 전 업무 복원</button>
      </div>
      {pcOnly && <p role="status">PC 업무를 계속할 수 있습니다. 휴대폰 동기화는 중지 상태를 유지합니다.</p>}
      {!candidate && <p>검증 가능한 초기화 백업이 없습니다. 동기화를 임의로 재개하지 않습니다.</p>}
    </>}
    {!held && safety && <p role="status">업무 복원 완료. 휴대폰 연결은 모바일 연결에서 다시 시작하세요.</p>}
    {safety && <details><summary>복원 직전 자료의 안전 사본</summary><p>{safety}</p><p>별도 보관된 암호화 사본입니다. 신규 자료를 자동 병합하지 않습니다. 이 사본의 선택 복원은 별도 검토가 필요합니다.</p></details>}
    <dialog ref={dialog} className="mc-dialog" aria-labelledby="business-restore-title" onCancel={event => { if (pending.current) event.preventDefault(); }}>
      <h3 id="business-restore-title">초기화 전 업무 복원</h3>
      <p className="mc-attention">초기화 후 새로 입력한 PC 자료는 안전 사본으로 보관되며, 현재 업무 자료는 백업 시점으로 바뀝니다. 자동 병합하지 않습니다.</p>
      <p>로그인·등록 휴대폰·현재 인증 상태는 유지합니다. 동기화 가능한 시점으로 복원된 경우에만 중지를 해제하며, 연결은 직접 다시 시작합니다.</p>
      <details><summary>복원할 암호화 백업</summary><p>{candidate}</p><p>백업 자체가 초기화 후 중지 상태이면 중지를 유지합니다. 더 이전 백업을 따로 선택·확인해야 합니다.</p></details>
      <label>계속하려면 ‘복원’을 정확히 입력하세요.
        <input aria-label="복원 확인 입력" value={confirmation} disabled={busy} autoComplete="off" onChange={e => setConfirmation(e.currentTarget.value)} style={{ display: "block", width: "100%", boxSizing: "border-box", font: "inherit", margin: "8px 0" }} />
      </label>
      <div className="mc-choices">
        <button ref={cancel} disabled={busy} onClick={() => dialog.current?.close()}>취소</button>
        <button disabled={busy || confirmation !== "복원"} onClick={() => void submit()}>{busy ? "복원 처리 중…" : "현재 자료를 백업하고 복원"}</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </dialog>
    {error && !dialog.current?.open && <p role="alert">{error}</p>}
  </section>;
}
