import { useCallback, useState } from "react";
import {
  authenticate,
  type AuthProvider,
  listLocalAccounts,
  localRecoveryStatus,
  requestCentralPasswordReset,
  resetLocalPassword,
} from "./auth";
import { CENTRAL_SIGNUP_OPEN, centralEnabled } from "./centralConfig";
import { loadRememberedEmail, saveRememberedEmail } from "./rememberEmail";

interface LoginScreenProps {
  onLogin: () => void;
}

export default function LoginScreen({ onLogin }: LoginScreenProps) {
  const central = centralEnabled();
  // 중앙 설정이 있는 빌드는 모아둠 계정이 기본. 기존 이 PC 계정은 연결 전까지 계속 로그인 가능.
  const [provider, setProvider] = useState<AuthProvider>(central ? "central" : "local");
  const [mode, setMode] = useState<"login" | "register">("login");
  const rememberedEmail = loadRememberedEmail();
  const [email, setEmail] = useState(rememberedEmail);
  const [rememberEmail, setRememberEmail] = useState(rememberedEmail !== "");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showRecovery, setShowRecovery] = useState(false);
  const [accounts, setAccounts] = useState<{ email: string; created_at?: string }[]>([]);
  const [recoveryEmail, setRecoveryEmail] = useState("");
  const [recoveryPw, setRecoveryPw] = useState("");
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoveryErr, setRecoveryErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const openRecovery = useCallback(async () => {
    if (provider === "central") {
      setErr(null);
      setMsg(null);
      if (!email.trim()) {
        setErr("비밀번호를 재설정할 모아둠 계정 이메일을 먼저 입력하세요.");
        return;
      }
      try {
        await requestCentralPasswordReset(email.trim().toLowerCase());
        setMsg("비밀번호 재설정 메일을 보냈습니다. 메일의 링크에서 새 비밀번호를 정한 뒤 로그인하세요.");
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
      return;
    }
    setShowRecovery(true);
    setRecoveryErr(null);
    setMsg(null);
    if (!(await localRecoveryStatus())) {
      setRecoveryErr("복구 불가: 이 컴퓨터에 저장된 계정이 없습니다.");
      return;
    }
    try {
      const found = await listLocalAccounts();
      setAccounts(found);
      setRecoveryEmail(found[0]?.email ?? "");
    } catch {
      setRecoveryErr("복구 중 오류가 발생했습니다.");
    }
  }, [provider, email]);

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
      setShowRecovery(false);
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
      const result = await authenticate(mode, normalizedEmail, pw, provider);
      saveRememberedEmail(normalizedEmail, rememberEmail);
      if (result.requiresEmailConfirmation) {
        setPw("");
        setMode("login");
        setMsg("확인 메일을 보냈습니다. 메일의 링크를 누른 뒤 여기서 로그인하세요. 이용권은 관리자가 발급합니다.");
        return;
      }
      onLogin();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container" style={{ maxWidth: 420, paddingTop: "80px" }}>
      <div className="animate-fade-in" style={{ textAlign: "center", marginBottom: "var(--space-8)" }}>
        <h1 className="text-3xl font-bold" style={{ color: "var(--primary-600)", marginBottom: "var(--space-2)" }}>
          모아둠
        </h1>
        <p className="text-secondary" style={{ fontSize: "var(--text-base)" }}>
          보험 설계사를 위한 AI 고객 관리 시스템
        </p>
      </div>

      <div className="card animate-slide-up">
        <div className="card-body">
          {central && (
            <div className="flex gap-2 mb-3" role="tablist" aria-label="로그인 방식">
              <button type="button" role="tab" aria-selected={provider === "central"}
                onClick={() => { setProvider("central"); if (!CENTRAL_SIGNUP_OPEN) setMode("login"); setErr(null); setMsg(null); setShowRecovery(false); }}
                className={provider === "central" ? "btn btn-secondary" : "btn btn-ghost"} style={{ flex: 1, fontSize: "var(--text-sm)" }}>
                모아둠 계정
              </button>
              <button type="button" role="tab" aria-selected={provider === "local"}
                onClick={() => { setProvider("local"); setMode("login"); setErr(null); setMsg(null); }}
                className={provider === "local" ? "btn btn-secondary" : "btn btn-ghost"} style={{ flex: 1, fontSize: "var(--text-sm)" }}>
                이 PC 기존 계정
              </button>
            </div>
          )}
          {/* 탭 전환 */}
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => {
                setMode("login");
                setErr(null);
                setMsg(null);
              }}
              className={mode === "login" ? "btn btn-primary" : "btn btn-ghost"}
              style={{ flex: 1 }}
            >
              로그인
            </button>
            {/* 중앙 빌드에서는 이 PC 전용 신규 가입을 열지 않는다(가입 체계는 모아둠 계정 하나).
                모아둠 계정 신규 가입도 안내 메일 발송 준비 전에는 닫는다(VITE_MOADOOM_SIGNUP_OPEN). */}
            {!(central && provider === "local") && !(central && provider === "central" && !CENTRAL_SIGNUP_OPEN) && (
              <button
                onClick={() => {
                  setMode("register");
                  setErr(null);
                  setMsg(null);
                }}
                className={mode === "register" ? "btn btn-primary" : "btn btn-ghost"}
                style={{ flex: 1 }}
              >
                회원가입
              </button>
            )}
          </div>

          {central && provider === "central" && !CENTRAL_SIGNUP_OPEN && (
            <p className="text-secondary" style={{ fontSize: "var(--text-sm)", margin: "0 0 var(--space-3)" }}>
              지금은 기존 모아둠 계정 로그인만 가능합니다. 신규 가입은 안내 메일 발송 준비 후 열립니다.
            </p>
          )}

          {central && provider === "local" && (
            <p className="text-secondary" role="note" style={{ fontSize: "var(--text-sm)", margin: "0 0 var(--space-3)" }}>
              이 PC 기존 계정으로 로그인하면 '자료 보존 모드'로 열립니다: 기존 고객 자료 조회와 내보내기만 할 수 있고,
              새 등록·수정·분석·AI 기능은 모아둠 계정으로 로그인해야 합니다. 이 PC 계정 비밀번호를 잊었다면 모아둠 계정으로
              로그인하세요(같은 PC의 고객 자료가 그대로 보입니다).
            </p>
          )}

          {/* 로그인/회원가입 폼 */}
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="input-group">
              <label htmlFor="email" className="input-label">
                이메일
              </label>
              <input
                id="email"
                type="email"
                className="input"
                placeholder="your@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>

            <div className="input-group">
              <label htmlFor="password" className="input-label">
                비밀번호
              </label>
              <input
                id="password"
                type="password"
                className="input"
                placeholder="6자 이상"
                value={pw}
                onChange={(e) => setPw(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>

            <label className="input-checkbox-label">
              <input
                type="checkbox"
                className="input-checkbox"
                checked={rememberEmail}
                onChange={(e) => setRememberEmail(e.target.checked)}
              />
              이메일 기억하기
            </label>

            <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy}>
              {busy ? "처리 중..." : mode === "login" ? (central && provider === "local" ? "자료 보존 모드로 열기" : "로그인") : "가입하고 시작"}
            </button>
          </form>

          {/* 메시지 */}
          {err && (
            <div className="mt-4 text-sm text-error" role="alert">
              {err}
            </div>
          )}
          {msg && (
            <div className="mt-4 text-sm text-success" role="status">
              {msg}
            </div>
          )}

          {/* 비밀번호 복구 — 중앙 빌드의 이 PC 계정은 인증 없는 로컬 재설정을 쓰지 않는다(서버도 차단) */}
          {central && provider === "local" ? null : !showRecovery ? (
            <button
              type="button"
              onClick={openRecovery}
              className="btn btn-ghost w-full mt-4"
              style={{ fontSize: "var(--text-sm)" }}
            >
              비밀번호를 잊으셨나요?
            </button>
          ) : (
            <div className="card mt-4" style={{ backgroundColor: "var(--neutral-50)" }}>
              <div className="card-body card-compact">
                <h3 className="font-semibold mb-3">계정 복구</h3>
                {accounts.length === 0 ? (
                  <p className="text-sm text-secondary">이 컴퓨터에 저장된 계정이 없습니다.</p>
                ) : (
                  <form onSubmit={submitRecovery} className="flex flex-col gap-3">
                    <div className="input-group">
                      <label className="input-label">계정 선택</label>
                      {accounts.map((account) => (
                        <label key={account.email} className="input-checkbox-label text-sm">
                          <input
                            type="radio"
                            className="input-checkbox"
                            name="recovery-account"
                            checked={recoveryEmail === account.email}
                            onChange={() => setRecoveryEmail(account.email)}
                          />
                          {account.email}
                          {account.created_at && (
                            <span className="text-secondary"> ({account.created_at.slice(0, 10)})</span>
                          )}
                        </label>
                      ))}
                    </div>

                    <div className="input-group">
                      <label htmlFor="new-password" className="input-label">
                        새 비밀번호
                      </label>
                      <input
                        id="new-password"
                        type="password"
                        className="input"
                        placeholder="6자 이상"
                        value={recoveryPw}
                        onChange={(e) => setRecoveryPw(e.target.value)}
                        autoComplete="new-password"
                      />
                    </div>

                    {recoveryErr && (
                      <div className="text-sm text-error" role="alert">
                        {recoveryErr}
                      </div>
                    )}

                    <div className="flex gap-2">
                      <button type="submit" className="btn btn-primary" disabled={recoveryBusy}>
                        {recoveryBusy ? "처리 중..." : "비밀번호 재설정"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowRecovery(false);
                          setRecoveryErr(null);
                        }}
                        className="btn btn-secondary"
                      >
                        취소
                      </button>
                    </div>
                  </form>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 보안 안내 */}
      <div className="text-center mt-6 text-sm text-secondary">
        <p>🔒 고객 정보는 100% 본인 PC에만 저장됩니다.</p>
        <p>{central ? "서버에는 계정·이용권 정보만 확인합니다 · 고객 자료 전송 없음" : "클라우드 전송 없음 · 완전한 프라이버시"}</p>
      </div>
    </div>
  );
}
