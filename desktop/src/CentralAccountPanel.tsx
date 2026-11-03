import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { getAuthProvider, linkLocalAccount } from "./auth";
import { CENTRAL_ADMIN_URL, centralEnabled } from "./centralConfig";
import { isOperator, type LicenseState } from "./licenseStatus";

const row: React.CSSProperties = { fontSize: "0.875rem", margin: "4px 0" };

/** 모아둠 계정(중앙) 안내: 기존 PC 계정 연결, 관리자 화면 열기. 고객 자료는 이동하지 않는다. */
export default function CentralAccountPanel({ lic }: { lic: (LicenseState & { offline?: boolean }) | null }) {
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!centralEnabled()) return null;
  const provider = getAuthProvider();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await linkLocalAccount(email, pw);
      setMsg({ ok: true, text: `이 PC 계정(${r.local_email})을 모아둠 계정에 연결했습니다. 고객 자료는 그대로입니다.` });
      setPw("");
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: 8 }}>
      <p style={row}>로그인 방식: {provider === "central" ? "모아둠 계정" : "이 PC 기존 계정"}</p>
      {provider === "central" && isOperator(lic) && !lic?.offline && (
        <button style={{ fontSize: "0.75rem" }} onClick={() => void openUrl(CENTRAL_ADMIN_URL)}>
          회원 이용권 관리 열기
        </button>
      )}
      {provider === "central" && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: "0.875rem" }}>이 PC 기존 계정 연결</summary>
          <p style={{ fontSize: "0.75rem", color: "#555" }}>
            예전에 이 PC에서 쓰던 계정의 비밀번호를 한 번 확인해 모아둠 계정과 연결합니다. 고객 자료는 옮기거나 지우지 않습니다.
          </p>
          <form onSubmit={submit}>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="기존 PC 계정 이메일"
              required style={{ display: "block", marginBottom: 6 }} />
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="기존 PC 계정 비밀번호"
              required style={{ display: "block", marginBottom: 6 }} autoComplete="current-password" />
            <button type="submit" disabled={busy}>{busy ? "확인 중…" : "연결"}</button>
          </form>
          {msg && <p role="status" style={{ fontSize: "0.75rem", color: msg.ok ? "#166534" : "#b00" }}>{msg.text}</p>}
        </details>
      )}
    </div>
  );
}
