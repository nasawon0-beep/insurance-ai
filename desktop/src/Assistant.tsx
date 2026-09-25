import { useEffect, useRef, useState } from "react";
import { useAssistant } from "./AssistantContext";

const EXAMPLES = [
  "이영희 보험료 얼마 내?",
  "최유진 만기 언제야?",
  "이번 달 생일인 고객 있어?",
  "만기 3개월 안에 오는 계약?",
  "월 보험료 다 합치면 얼마야?",
  "후속 연락해야 할 사람?",
];

export default function AssistantScreen({ onOpenCustomer }: { onOpenCustomer: (id: string) => void }) {
  const { messages, busy, err, sendQuestion, clearMessages } = useAssistant();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, busy]);

  const send = async (raw?: string) => {
    const question = (raw ?? input).trim();
    if (!question || busy) return;
    
    await sendQuestion(question, messages);
    setInput("");
  };

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: 20, display: "flex", flexDirection: "column", height: "calc(100vh - 110px)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid #e5e5e5" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 600, color: "var(--color-text-primary)" }}>AI 문의</h2>
          <span style={{ fontSize: "0.875rem", color: "var(--color-text-secondary)" }}>고객 데이터를 자연어로 물어보세요</span>
        </div>
        {messages.length > 0 && (
          <button 
            onClick={clearMessages}
            style={{ 
              marginLeft: "auto", 
              fontSize: 13, 
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid #e5e5e5",
              background: "var(--color-bg-surface)",
              color: "#525252",
              cursor: "pointer",
              fontWeight: 500
            }}
          >
            대화 초기화
          </button>
        )}
      </div>

      <div
        ref={scrollRef}
        style={{ 
          flex: 1, 
          overflowY: "auto", 
          borderRadius: 12, 
          padding: 16, 
          background: "var(--color-bg-surface)",
          marginBottom: 12
        }}
      >
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "32px 0" }}>
            <p className="example-header" style={{ marginTop: 0, marginBottom: 16, fontSize: "1rem", fontWeight: 500 }}>예시 질문을 선택해보세요</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => send(ex)}
                  disabled={busy}
                  className="example-button"
                  style={{ 
                    fontSize: "0.875rem", 
                    padding: "8px 14px", 
                    borderRadius: 20, 
                    cursor: busy ? "not-allowed" : "pointer",
                    transition: "all 0.15s",
                    fontWeight: 500,
                    opacity: busy ? 0.5 : 1
                  }}
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", margin: "12px 0" }}>
            <div
              style={{
                maxWidth: "75%",
                padding: "10px 14px",
                borderRadius: m.role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
                whiteSpace: "pre-wrap",
                lineHeight: 1.5,
                background: m.role === "user" ? "var(--primary-600)" : "var(--color-bg-base)",
                color: m.role === "user" ? "#fff" : "var(--color-text-primary)",
                border: "1px solid var(--color-border-default)",
                boxShadow: m.role === "user" 
                  ? "0 1px 2px rgba(37,99,235,0.1)" 
                  : "var(--shadow-sm)",
                fontSize: "1rem"
              }}
            >
              <div>{m.content}</div>
              {m.role === "assistant" && m.no_data && (
                <div style={{ marginTop: 8, fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
                  <span style={{ background: "var(--color-bg-base)", borderRadius: 6, padding: "3px 8px", border: "1px solid #e5e5e5" }}>
                    데이터에서 찾지 못함
                  </span>
                </div>
              )}
              {m.role === "assistant" && (m.used_customers?.length ?? 0) > 0 && (
                <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {m.used_customers!.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => onOpenCustomer(c.id)}
                      style={{ 
                        fontSize: "0.75rem", 
                        padding: "4px 10px", 
                        borderRadius: 12, 
                        border: "1px solid #dbeafe", 
                        background: "var(--color-bg-surface)", 
                        cursor: "pointer",
                        color: "#1d4ed8",
                        fontWeight: 500,
                        transition: "all 0.15s"
                      }}
                    >
                      👤 {c.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div style={{ display: "flex", justifyContent: "flex-start", margin: "12px 0" }}>
            <div style={{
              padding: "10px 14px",
              borderRadius: "16px 16px 16px 4px",
              background: "var(--color-bg-base)",
              border: "1px solid var(--color-border-default)",
              boxShadow: "var(--shadow-sm)"
            }}>
              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <div style={{ 
                  width: 6, 
                  height: 6, 
                  borderRadius: "50%", 
                  background: "#a3a3a3",
                  animation: "pulse 1.4s ease-in-out infinite"
                }} />
                <div style={{ 
                  width: 6, 
                  height: 6, 
                  borderRadius: "50%", 
                  background: "#a3a3a3",
                  animation: "pulse 1.4s ease-in-out 0.2s infinite"
                }} />
                <div style={{ 
                  width: 6, 
                  height: 6, 
                  borderRadius: "50%", 
                  background: "#a3a3a3",
                  animation: "pulse 1.4s ease-in-out 0.4s infinite"
                }} />
              </div>
              <p style={{ fontSize: "0.75rem", color: "#a3a3a3", margin: "6px 0 0", fontStyle: "italic" }}>
                AI가 데이터를 확인하는 중…
              </p>
            </div>
          </div>
        )}
      </div>

      {err && (
        <div style={{ 
          padding: "10px 14px", 
          background: "var(--color-bg-surface)", 
          border: "1px solid #fecaca", 
          borderRadius: 8, 
          color: "#991b1b", 
          fontSize: "0.875rem",
          marginBottom: 12
        }}>
          오류: {err}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, background: "var(--color-bg-surface)", borderRadius: 12, border: "1px solid var(--color-border-default)", padding: 8, boxShadow: "var(--shadow-sm)" }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="예: 이영희 보험료 얼마 내? (Enter 전송, Shift+Enter 줄바꿈)"
          rows={2}
          className="assistant-input"
          style={{ 
            flex: 1, 
            padding: 10, 
            resize: "none", 
            boxSizing: "border-box", 
            fontFamily: "inherit", 
            fontSize: "1rem",
            border: "1px solid var(--color-border-default)",
            outline: "none",
            background: "transparent"
          }}
        />
        <button 
          onClick={() => send()} 
          disabled={busy || !input.trim()} 
          style={{ 
            padding: "8px 20px",
            background: busy || !input.trim() ? "var(--color-bg-base)" : "var(--primary-600)",
            color: busy || !input.trim() ? "#a3a3a3" : "#fff",
            border: "1px solid var(--color-border-default)",
            borderRadius: 8,
            cursor: busy || !input.trim() ? "not-allowed" : "pointer",
            fontSize: "0.875rem",
            fontWeight: 600,
            transition: "all 0.15s",
            alignSelf: "flex-end"
          }}
        >
          {busy ? "전송 중…" : "전송"}
        </button>
      </div>

      <p style={{ fontSize: "0.75rem", color: "#a3a3a3", marginTop: 12, textAlign: "center" }}>
        특정 보험의 약관 내용은 고객 상세 화면의 '이 고객 약관에 질문'에서 확인하세요
      </p>
      
      <style>{`
        @keyframes pulse {
          0%, 100% {
            opacity: 0.3;
            transform: scale(0.8);
          }
          50% {
            opacity: 1;
            transform: scale(1.2);
          }
        }

        /* 라이트모드 기본 스타일 */
        .example-header {
          color: var(--color-text-secondary);
        }

        .example-button {
          border: 1px solid var(--color-border-default);
          background: var(--color-bg-base);
          color: var(--color-text-primary);
        }

        .example-button:hover:not(:disabled) {
          background: var(--color-bg-surface);
          border-color: var(--color-border-hover);
        }

        .assistant-input {
          color: var(--color-text-primary);
        }

        .assistant-input::placeholder {
          color: var(--color-text-secondary);
        }

        /* 다크모드 스타일 */
        @media (prefers-color-scheme: dark) {
          .example-header {
            color: var(--color-text-secondary);
          }

          .example-button {
            border: 1px solid var(--color-border-default);
            background: var(--color-bg-base);
            color: var(--color-text-primary);
          }

          .example-button:hover:not(:disabled) {
            background: var(--color-bg-surface);
            border-color: var(--color-border-hover);
          }

          .assistant-input {
            color: var(--color-text-primary);
          }

          .assistant-input::placeholder {
            color: var(--color-text-secondary);
          }
        }
      `}</style>
    </div>
  );
}
