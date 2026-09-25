import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { track } from "./usage";
import { ThemeProvider } from "./ThemeContext";
import "./styles/index.css";

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

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ThemeProvider>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </ThemeProvider>
  </React.StrictMode>,
);
