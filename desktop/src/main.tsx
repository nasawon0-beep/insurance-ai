import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { track } from "./usage";
import { ThemeProvider } from "./ThemeContext";
import { initializeRuntimeProfile } from "./config";
import "./styles/index.css";
import "./styles/navy.css";
import "./styles/legacy-surfaces.css";

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { crashed: boolean }
> {
  state = { crashed: false };

  static getDerivedStateFromError() {
    return { crashed: true };
  }

  componentDidCatch(error: unknown) {
    try {
      track("error", { where: "react", status: 500 });
    } catch {
      /* noop */
    }
    console.error("React 렌더 오류:", error);
  }

  render() {
    if (this.state.crashed) {
      return (
        <div style={{ maxWidth: 480, margin: "80px auto", textAlign: "center", color: "#444" }}>
          <h3>화면을 그리는 중 문제가 발생했습니다</h3>
          <p style={{ fontSize: 13, color: "#888" }}>앱을 다시 시작해 주세요.</p>
          <button onClick={() => location.reload()}>새로고침</button>
        </div>
      );
    }
    return this.props.children;
  }
}

const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement);

void initializeRuntimeProfile()
  .then(() => {
    root.render(
      <React.StrictMode>
        <ThemeProvider>
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
        </ThemeProvider>
      </React.StrictMode>,
    );
  })
  .catch((error) => {
    console.error("runtime profile 검증 실패:", error);
    root.render(
      <div style={{ maxWidth: 560, margin: "80px auto", textAlign: "center", color: "#444" }}>
        <h3>안전한 실행 환경을 확인할 수 없습니다</h3>
        <p style={{ fontSize: 13, color: "#888" }}>운영 데이터로 전환하지 않고 앱 시작을 중단했습니다.</p>
      </div>,
    );
  });
