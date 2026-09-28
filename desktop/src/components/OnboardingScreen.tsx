import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import "./OnboardingScreen.css";

type OnboardingStatus =
  | "checking"
  | "ready"
  | "downloading"
  | "installing"
  | "checking-models"
  | "downloading-models"
  | "completed"
  | "error";

interface OnboardingState {
  status: OnboardingStatus;
  progress: number;
  message: string;
  currentModel?: string;
  modelProgress?: number;
  downloadedModels?: string[];
  error?: string;
}

type OnboardingScreenProps = {
  onComplete: () => void;
  onOllamaInstalled?: () => void;
};

type ModelDownloadPayload = {
  model?: string;
  status?: string;
  progress?: number;
  downloaded?: string;
  total?: string;
  message?: string;
};

const REQUIRED_MODELS = ["bge-m3:latest", "qwen2.5:7b"] as const;
const MODEL_LABELS: Record<string, string> = {
  "bge-m3:latest": "bge-m3 (600MB)",
  "qwen2.5:7b": "qwen2.5:7b (4.7GB)",
};

const INITIAL_STATE: OnboardingState = {
  status: "checking",
  progress: 0,
  message: "Ollama 설치 확인 중...",
  downloadedModels: [],
};

const VALID_STATUSES = new Set<OnboardingStatus>([
  "checking",
  "ready",
  "downloading",
  "installing",
  "checking-models",
  "downloading-models",
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

function parseModelDownloadPayload(payload: unknown): ModelDownloadPayload | null {
  if (!payload || typeof payload !== "object") return null;
  const data = payload as Record<string, unknown>;
  return {
    model: typeof data.model === "string" ? data.model : undefined,
    status: typeof data.status === "string" ? data.status : undefined,
    progress: normalizeProgress(data.progress),
    downloaded: typeof data.downloaded === "string" ? data.downloaded : undefined,
    total: typeof data.total === "string" ? data.total : undefined,
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export default function OnboardingScreen({ onComplete, onOllamaInstalled }: OnboardingScreenProps) {
  const [state, setState] = useState<OnboardingState>(INITIAL_STATE);
  const unlistenRef = useRef<UnlistenFn | null>(null);
  const progress = normalizeProgress(state.progress);
  const modelProgress = normalizeProgress(state.modelProgress ?? 0);
  const isBusy = ["downloading", "installing", "checking-models", "downloading-models"].includes(state.status);

  const cleanupProgressListener = useCallback(() => {
    if (unlistenRef.current) {
      unlistenRef.current();
      unlistenRef.current = null;
    }
  }, []);

  const completeOnboarding = useCallback(() => {
    cleanupProgressListener();
    localStorage.setItem("iai.ollamaOnboardingDone", "1");
    localStorage.removeItem("iai.ollamaOnboardingSkipped");
    onComplete();
  }, [cleanupProgressListener, onComplete]);

  const downloadModels = useCallback(
    async (models: string[]) => {
      cleanupProgressListener();
      setState({
        status: "downloading-models",
        progress: 0,
        message: "모델 다운로드 시작...",
        currentModel: models[0],
        modelProgress: 0,
        downloadedModels: [],
      });

      const downloadedModels: string[] = [];

      try {
        unlistenRef.current = await listen("model-download-progress", (event) => {
          const payload = parseModelDownloadPayload(event.payload);
          if (!payload?.model) return;

          setState((current) => ({
            ...current,
            currentModel: payload.model,
            modelProgress: payload.progress ?? current.modelProgress,
            message: `${payload.model} - ${payload.message ?? "다운로드 중..."}`,
          }));

          if (payload.status === "completed") {
            setState((current) => ({
              ...current,
              downloadedModels: Array.from(new Set([...(current.downloadedModels ?? []), payload.model!])),
            }));
          }
        });

        for (let index = 0; index < models.length; index += 1) {
          const model = models[index];
          setState((current) => ({
            ...current,
            currentModel: model,
            modelProgress: 0,
            message: `${model} 다운로드 준비 중...`,
          }));

          await invoke("download_model", { modelName: model });
          downloadedModels.push(model);
          const overallProgress = Math.round(((index + 1) / models.length) * 100);
          setState((current) => ({
            ...current,
            progress: overallProgress,
            modelProgress: 100,
            downloadedModels: Array.from(new Set([...(current.downloadedModels ?? []), model])),
          }));
        }

        cleanupProgressListener();
        setState({
          status: "completed",
          progress: 100,
          message: "모든 모델 설치 완료!",
          modelProgress: 100,
          downloadedModels,
        });
      } catch (error) {
        cleanupProgressListener();
        setState((current) => ({
          ...current,
          status: "error",
          message: `${current.currentModel ?? "모델"} 다운로드 실패`,
          error: toErrorMessage(error),
        }));
      }
    },
    [cleanupProgressListener],
  );

  const checkModels = useCallback(async () => {
    cleanupProgressListener();
    setState((current) => ({ ...current, status: "checking-models", message: "모델 확인 중...", progress: 0 }));

    try {
      const installed = await invoke<string[]>("check_models_installed");
      const missing = REQUIRED_MODELS.filter((model) => !installed.includes(model));

      if (missing.length === 0) {
        setState({
          status: "completed",
          progress: 100,
          message: "완료",
          modelProgress: 100,
          downloadedModels: [...REQUIRED_MODELS],
        });
        return;
      }

      await downloadModels(missing);
    } catch (error) {
      setState({
        status: "error",
        progress: 0,
        message: "모델 확인 실패",
        error: toErrorMessage(error),
      });
    }
  }, [cleanupProgressListener, downloadModels]);

  const checkOllama = useCallback(async () => {
    setState(INITIAL_STATE);
    try {
      const installed = await invoke<boolean>("check_ollama_installed");
      if (installed) {
        await checkModels();
        return;
      }
      setState({ status: "ready", progress: 0, message: "Ollama를 설치하시겠어요?", downloadedModels: [] });
    } catch (error) {
      setState({
        status: "error",
        progress: 0,
        message: "확인 실패",
        error: toErrorMessage(error),
      });
    }
  }, [checkModels]);

  useEffect(() => {
    void checkOllama();
    return cleanupProgressListener;
  }, [checkOllama, cleanupProgressListener]);

  const installOllama = async () => {
    cleanupProgressListener();
    setState({ status: "downloading", progress: 0, message: "다운로드 준비 중...", downloadedModels: [] });

    try {
      unlistenRef.current = await listen("ollama-install-progress", (event) => {
        const nextState = parseProgressPayload(event.payload);
        if (!nextState) return;
        setState((current) => ({ ...current, ...nextState }));
        if (nextState.status === "error") {
          cleanupProgressListener();
        }
      });

      await invoke("install_ollama");
      cleanupProgressListener();
      onOllamaInstalled?.();
      await checkModels();
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

  const skipModels = () => {
    cleanupProgressListener();
    localStorage.setItem("iai.modelOnboardingSkipped", "1");
    localStorage.setItem("iai.ollamaOnboardingDone", "1");
    onComplete();
  };

  const cancelInstall = () => {
    cleanupProgressListener();
    setState({ status: "ready", progress: 0, message: "설치를 취소했습니다. 나중에 다시 설치할 수 있습니다.", downloadedModels: [] });
  };

  const retryInstall = () => {
    if (state.status === "error" && state.currentModel) {
      void checkModels();
      return;
    }
    void installOllama();
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

  const renderSteps = () => (
    <div className="onboarding-steps" aria-label="초기 설정 단계">
      <div className="onboarding-step completed">
        <span className="icon">✓</span>
        <span>Ollama 설치</span>
      </div>
      <div className={`onboarding-step ${state.status === "completed" ? "completed" : "active"}`}>
        <span className="icon">{state.status === "completed" ? "✓" : "●"}</span>
        <span>AI 모델 다운로드</span>
        <span className="time">약 15분</span>
      </div>
      <div className={`onboarding-step ${state.status === "completed" ? "completed" : "pending"}`}>
        <span className="icon">{state.status === "completed" ? "✓" : "○"}</span>
        <span>설정 완료</span>
      </div>
    </div>
  );

  const renderModelsList = () => {
    const downloaded = state.downloadedModels ?? [];
    return (
      <div className="onboarding-models-list">
        {REQUIRED_MODELS.map((model) => {
          const isDone = downloaded.includes(model);
          const isCurrent = state.currentModel === model && !isDone;
          return (
            <div key={model} className={`onboarding-model ${isDone ? "completed" : isCurrent ? "downloading" : "pending"}`}>
              <span className="icon">{isDone ? "✓" : isCurrent ? "●" : "○"}</span>
              <span>{MODEL_LABELS[model]}</span>
            </div>
          );
        })}
      </div>
    );
  };

  const renderModelDownload = () => (
    <div className="onboarding-body" aria-live="polite">
      <h2>AI 모델 설치 중</h2>
      {renderSteps()}
      <div className="onboarding-current-task">
        <p className="onboarding-model-name">{state.currentModel ?? "AI 모델"} 다운로드 중...</p>
        <div className="onboarding-progress-bar">
          <div className="onboarding-progress-fill" style={{ width: `${modelProgress}%` }} />
        </div>
        <p className="onboarding-progress-text">{state.message}</p>
      </div>
      {renderModelsList()}
      <p className="onboarding-eta">전체 소요 시간: 약 15분</p>
      <div className="onboarding-actions">
        <button className="onboarding-button onboarding-button-secondary" type="button" onClick={skipModels}>
          백그라운드
        </button>
        <button className="onboarding-button onboarding-button-secondary" type="button" onClick={skipModels}>
          나중에
        </button>
      </div>
    </div>
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
      case "checking-models":
        return (
          <div className="onboarding-body onboarding-centered" role="status" aria-live="polite">
            <div className="onboarding-spinner" aria-hidden="true" />
            <p className="onboarding-message">설치된 모델 확인 중...</p>
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
      case "installing":
        return (
          <div className="onboarding-body onboarding-centered" aria-live="polite">
            <div className="onboarding-icon onboarding-pulse" aria-hidden="true">🤖</div>
            <h2>Ollama 설치 중</h2>
            {renderProgress()}
            <p className="onboarding-message onboarding-message-animated">{state.message}</p>
            {state.status === "downloading" ? (
              <button className="onboarding-button onboarding-button-secondary" type="button" onClick={cancelInstall}>
                취소
              </button>
            ) : (
              <p className="onboarding-note">잠시만 기다려 주세요...</p>
            )}
          </div>
        );
      case "downloading-models":
        return renderModelDownload();
      case "completed":
        return (
          <div className="onboarding-body onboarding-centered">
            <div className="onboarding-icon" aria-hidden="true">✅</div>
            <h2>설정 완료!</h2>
            <p className="onboarding-lead">Insurance AI를 사용할 준비가 되었습니다</p>
            <button className="onboarding-button onboarding-button-primary" type="button" onClick={completeOnboarding}>
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
              <button className="onboarding-button onboarding-button-secondary" type="button" onClick={skipModels}>
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
