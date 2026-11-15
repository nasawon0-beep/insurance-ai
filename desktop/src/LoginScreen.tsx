import MaintenancePanel from './MaintenancePanel';
import {invoke} from '@tauri-apps/api/core';
import {centralClient} from './centralClient';
import { useCallback, useState } from "react";
import {
  authenticate,
  type AuthProvider,
  requestCentralPasswordReset,
} from "./auth";
import { CENTRAL_SIGNUP_OPEN, centralEnabled } from "./centralConfig";
import { loadRememberedEmail, saveRememberedEmail } from "./rememberEmail";

interface LoginScreenProps {
  onLogin: () => void;
}

export default function LoginScreen({ onLogin }: LoginScreenProps) {
  const central = centralEnabled();
  const [provider] = useState<AuthProvider>("central");
  const [mode, setMode] = useState<"login" | "register">("login");
  const rememberedEmail = loadRememberedEmail();
  const [email, setEmail] = useState(rememberedEmail);
  const [rememberEmail, setRememberEmail] = useState(rememberedEmail !== "");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [maintenanceOnly,setMaintenanceOnly]=useState(false);
  const [maintenanceReady,setMaintenanceReady]=useState(false);
  const [err, setErr] = useState<string | null>(null);

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
  }, [provider, email]);

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
      if(maintenanceOnly&&mode==='login'){
        const status=await invoke<{available:boolean;message:string}>('maintenance_status');
        if(!status.available)throw new Error(status.message);
        const expectedEpoch=await invoke<number>('account_session_generation');
        const {data,error}=await centralClient().auth.signInWithPassword({email:normalizedEmail,password:pw});
        if(error||!data.session)throw new Error('온라인 모아둠 로그인에 실패했습니다.');
        await invoke('account_session_activate',{accessToken:data.session.access_token,expectedSubject:data.session.user.id,expectedEpoch,maintenanceOnly:true});
        setPw('');setMaintenanceReady(true);return; // Do not start the normal App/business bootstrap.
      }
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

  if(maintenanceReady)return <div className="container" style={{maxWidth:760,padding:32}}>
    <h1>유지보수 전용 로그인</h1><p>업무공간과 업무 엔진을 만들지 않았습니다. 완료 후 앱을 다시 시작하세요. 기존 업무공간의 자동 병합·덮어쓰기는 하지 않습니다.</p>
    <MaintenancePanel/>
    <button onClick={async()=>{try{await invoke('account_session_close');await centralClient().auth.signOut();setMaintenanceReady(false);setMsg('로그아웃했습니다. 업무 시작은 앱을 다시 실행한 뒤 진행하세요.');}catch(e){setErr(String(e));}}}>작업 취소·로그아웃</button>
    {err&&<p role="alert">{err}</p>}
  </div>;
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
          <p>모아둠 계정으로 로그인</p>
          {!central && <p role="alert">중앙 인증 배포 설정을 확인해야 합니다. 기존 자료는 삭제되지 않습니다.</p>}
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
            {central && CENTRAL_SIGNUP_OPEN && (
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
              지금은 기존 모아둠 계정 로그인만 가능합니다. 신규 가입은 모아둠 사이트에서 안내합니다.
            </p>
          )}

          <p className="text-secondary">소유권이 확인되지 않은 이전 자료는 자동 연결하지 않습니다.</p>

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

            {mode==='login'&&<label><input type="checkbox" checked={maintenanceOnly} disabled={busy} onChange={e=>setMaintenanceOnly(e.target.checked)}/>이전 자료 검토 전용 로그인 (업무공간 생성·업무 시작 안 함)</label>}
            <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy || !central}>
              {busy ? "처리 중..." : mode === "login" ? "로그인" : "가입하고 시작"}
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

          {central && (
            <button
              type="button"
              onClick={openRecovery}
              className="btn btn-ghost w-full mt-4"
              style={{ fontSize: "var(--text-sm)" }}
            >
              비밀번호를 잊으셨나요?
            </button>
          )}
        </div>
      </div>

      <MaintenancePanel readOnly/>
      {/* 보안 안내 */}
      <div className="text-center mt-6 text-sm text-secondary">
        <p>🔒 고객 정보는 100% 본인 PC에만 저장됩니다.</p>
        <p>{central ? "서버에는 계정·이용권 정보만 확인합니다 · 고객 자료 전송 없음" : "클라우드 전송 없음 · 완전한 프라이버시"}</p>
      </div>
    </div>
  );
}
