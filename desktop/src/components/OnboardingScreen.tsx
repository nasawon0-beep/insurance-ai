import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import "./OnboardingScreen.css";

type OnboardingStatus = "checking" | "ready" | "downloading" | "installing" | "completed" | "error";

interface OnboardingState {
  status: OnboardingStatus;
  progress: number;
  message: string;
  error?: string;
}

type OnboardingScreenProps = {
  onComplete: () => void;
};

const INITIAL_STATE: OnboardingState = {
  status: "checking",
  progress: 0,
  message: "Ollama 설치 확인 중...",
};

const VALID_STATUSES = new Set<OnboardingStatus>([
  "checking",
  "ready",
  "downloading",
  "installing",
  "completed",
  "error",
]);

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function isStatus(value: unknown): value is OnboardingStatus {
  return typeof value === "string" && VALID_STATUSES.has(value as OnboardingStatus);
}

function normalizeProgress(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
}

function parseProgressPayload(payload: unknown): OnboardingState | null {
  if (!payload || typeof payload !== "object") return null;
  const data = payload as Record<string, unknown>;
  if (!isStatus(data.status)) return null;

  return {
    status: data.status,
    progress: normalizeProgress(data.progress),
    message: typeof data.message === "string" ? data.message : "진행 중...",
    error: typeof data.error === "string" ? data.error : undefined,
  };
}

export default function OnboardingScreen({ onComplete }: OnboardingScreenProps) {
  const [state, setState] = useState<OnboardingState>(INITIAL_STATE);
  const unlistenRef = useRef<UnlistenFn | null>(null);
  const progress = normalizeProgress(state.progress);
  const isBusy = state.status === "downloading" || state.status === "installing";

  const cleanupProgressListener = useCallback(() => {
    if (unlistenRef.current) {
      unlistenRef.current();
      unlistenRef.current = null;
    }
  }, []);

  const completeOnboarding = useCallback(() => {
    cleanupProgressListener();
    localStorage.setItem("iai.ollamaOnboardingDone", "1");
    onComplete();
  }, [cleanupProgressListener, onComplete]);

  const checkOllama = useCallback(async () => {
    setState(INITIAL_STATE);
    try {
      const installed = await invoke<boolean>("check_ollama_installed");
      if (installed) {
        setState({ status: "completed", progress: 100, message: "완료" });
        completeOnboarding();
        return;
      }
      setState({ status: "ready", progress: 0, message: "Ollama를 설치하시겠어요?" });
    } catch (error) {
      setState({
        status: "error",
        progress: 0,
        message: "확인 실패",
        error: toErrorMessage(error),
      });
    }
  }, [completeOnboarding]);

  useEffect(() => {
    void checkOllama();
    return cleanupProgressListener;
  }, [checkOllama, cleanupProgressListener]);

  const installOllama = async () => {
    cleanupProgressListener();
    setState({ status: "downloading", progress: 0, message: "다운로드 준비 중..." });

    try {
      unlistenRef.current = await listen("ollama-install-progress", (event) => {
        const nextState = parseProgressPayload(event.payload);
        if (!nextState) return;
        setState(nextState);
        if (nextState.status === "completed" || nextState.status === "error") {
          cleanupProgressListener();
        }
      });

      await invoke("install_ollama");
      setState((current) =>
        current.status === "completed"
          ? current
          : { status: "completed", progress: 100, message: "설치 완료" },
      );
      cleanupProgressListener();
    } catch (error) {
      cleanupProgressListener();
      setState({
        status: "error",
        progress: 0,
        message: "설치 실패",
        error: toErrorMessage(error),
      });
    }
  };

  const skipOnboarding = () => {
    cleanupProgressListener();
    localStorage.setItem("iai.ollamaOnboardingSkipped", "1");
    onComplete();
  };

  const cancelInstall = () => {
    cleanupProgressListener();
    setState({ status: "ready", progress: 0, message: "설치를 취소했습니다. 나중에 다시 설치할 수 있습니다." });
  };

  const retryInstall = () => {
    void installOllama();
  };

  const goToMain = () => {
    completeOnboarding();
  };

  const renderProgress = () => (
    <>
      <div className="onboarding-progress-row" aria-hidden="true">
        <div className="onboarding-progress-bar">
          <div className="onboarding-progress-fill" style={{ width: `${progress}%` }} />
        </div>
        <span className="onboarding-progress-value">{progress}%</span>
      </div>
      <div className="onboarding-progress-sr" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
        설치 진행률 {progress}%
      </div>
    </>
  );

  const renderContent = () => {
    switch (state.status) {
      case "checking":
        return (
          <div className="onboarding-body onboarding-centered" role="status" aria-live="polite">
            <div className="onboarding-spinner" aria-hidden="true" />
            <p className="onboarding-message">Ollama 설치 확인 중...</p>
          </div>
        );
      case "ready":
        return (
          <div className="onboarding-body onboarding-centered">
            <div className="onboarding-icon onboarding-pulse" aria-hidden="true">🤖</div>
            <h2>AI 기능을 사용하려면</h2>
            <p className="onboarding-lead">Ollama 설치가 필요합니다</p>
            <p className="onboarding-size">다운로드 크기: 약 150MB</p>
            <div className="onboarding-actions">
              <button className="onboarding-button onboarding-button-primary" type="button" onClick={() => void installOllama()}>
                설치하기
              </button>
              <button className="onboarding-button onboarding-button-secondary" type="button" onClick={skipOnboarding}>
                나중에
              </button>
            </div>
          </div>
        );
      case "downloading":
        return (
          <div className="onboarding-body onboarding-centered" aria-live="polite">
            <div className="onboarding-icon onboarding-pulse" aria-hidden="true">🤖</div>
            <h2>Ollama 설치 중</h2>
            {renderProgress()}
            <p className="onboarding-message onboarding-message-animated">{state.message}</p>
            <button className="onboarding-button onboarding-button-secondary" type="button" onClick={cancelInstall}>
              취소
            </button>
          </div>
        );
      case "installing":
        return (
          <div className="onboarding-body onboarding-centered" aria-live="polite">
            <div className="onboarding-icon onboarding-pulse" aria-hidden="true">🤖</div>
            <h2>Ollama 설치 중</h2>
            {renderProgress()}
            <p className="onboarding-message onboarding-message-animated">{state.message}</p>
            <p className="onboarding-note">잠시만 기다려 주세요...</p>
          </div>
        );
      case "completed":
        return (
          <div className="onboarding-body onboarding-centered">
            <div className="onboarding-icon" aria-hidden="true">✅</div>
            <h2>설치 완료!</h2>
            <p className="onboarding-lead">Insurance AI를 사용할 준비가 되었습니다</p>
            <button className="onboarding-button onboarding-button-primary" type="button" onClick={goToMain}>
              시작하기
            </button>
          </div>
        );
      case "error":
        return (
          <div className="onboarding-body onboarding-centered" role="alert">
            <div className="onboarding-icon" aria-hidden="true">⚠️</div>
            <h2>설치 실패</h2>
            <p className="onboarding-error">{state.message}</p>
            {state.error && <p className="onboarding-error-detail">{state.error}</p>}
            <div className="onboarding-actions">
              <button className="onboarding-button onboarding-button-primary" type="button" onClick={retryInstall} disabled={isBusy}>
                다시 시도
              </button>
              <button className="onboarding-button onboarding-button-secondary" type="button" onClick={skipOnboarding}>
                나중에
              </button>
            </div>
          </div>
        );
    }
  };

  return (
    <main className="onboarding-screen">
      <section className="onboarding-card" aria-labelledby="onboarding-title">
        <header className="onboarding-header">
          <h1 id="onboarding-title">Insurance AI 초기 설정</h1>
        </header>
        {renderContent()}
      </section>
    </main>
  );
}
