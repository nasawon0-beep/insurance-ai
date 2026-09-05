import { useEffect, useRef, useState } from "react";
import { engineFetch } from "./engine";
import { formatErrorDetail } from "./errorDetail";

async function api(path: string, init?: RequestInit) {
  const res = await engineFetch(path, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(formatErrorDetail(body?.detail) ?? `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

type UsedCustomer = { id: string; name: string };
type Message = {
  role: "user" | "assistant";
  content: string;
  used_customers?: UsedCustomer[];
  no_data?: boolean;
};

const EXAMPLES = [
  "나상원 보험료 얼마 내?",
  "정지은 만기 언제야?",
  "이번 달 생일인 고객 있어?",
  "만기 3개월 안에 오는 계약?",
  "월 보험료 다 합치면 얼마야?",
  "후속 연락해야 할 사람?",
];

export default function AssistantScreen({ onOpenCustomer }: { onOpenCustomer: (id: string) => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, busy]);

  const send = async (raw?: string) => {
    const question = (raw ?? input).trim();
    if (!question || busy) return;
    const history = messages.slice(-6).map((m) => ({ role: m.role, content: m.content }));
    setMessages((ms) => [...ms, { role: "user", content: question }]);
    setInput("");
    setErr(null);
    setBusy(true);
    try {
      const res = await api("/assistant/ask", {
        method: "POST",
        body: JSON.stringify({ question, history }),
      });
      setMessages((ms) => [
        ...ms,
        {
          role: "assistant",
          content: res.answer,
          used_customers: res.used_customers ?? [],
          no_data: !!res.no_data,
        },
      ]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 16, display: "flex", flexDirection: "column", height: "calc(100vh - 110px)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>AI 문의</h2>
        <span style={{ marginLeft: 10, fontSize: 12, color: "#888" }}>고객 데이터를 자연어로 물어보세요</span>
        {messages.length > 0 && (
          <button onClick={() => setMessages([])} style={{ marginLeft: "auto", fontSize: 12 }}>
            대화 초기화
          </button>
        )}
      </div>

      <div
        ref={scrollRef}
        style={{ flex: 1, overflowY: "auto", border: "1px solid #e5e5e5", borderRadius: 8, padding: 12, background: "#fafafa" }}
      >
        {messages.length === 0 && (
          <div style={{ color: "#666" }}>
            <p style={{ marginTop: 0 }}>예시 질문을 눌러보세요:</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => send(ex)}
                  disabled={busy}
                  style={{ fontSize: 13, padding: "6px 10px", borderRadius: 16, border: "1px solid #ccc", background: "#fff", cursor: "pointer" }}
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", margin: "8px 0" }}>
            <div
              style={{
                maxWidth: "78%",
                padding: "8px 12px",
                borderRadius: 12,
                whiteSpace: "pre-wrap",
                lineHeight: 1.5,
                background: m.role === "user" ? "#2563eb" : "#fff",
                color: m.role === "user" ? "#fff" : "#111",
                border: m.role === "user" ? "none" : "1px solid #e0e0e0",
              }}
            >
              <div>{m.content}</div>
              {m.role === "assistant" && m.no_data && (
                <div style={{ marginTop: 6, fontSize: 11, color: "#888" }}>
                  <span style={{ background: "#eee", borderRadius: 4, padding: "1px 6px" }}>데이터에서 찾지 못함</span>
                </div>
              )}
              {m.role === "assistant" && (m.used_customers?.length ?? 0) > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {m.used_customers!.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => onOpenCustomer(c.id)}
                      style={{ fontSize: 12, padding: "2px 8px", borderRadius: 12, border: "1px solid #cbd5e1", background: "#f1f5f9", cursor: "pointer" }}
                    >
                      참조: {c.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div style={{ color: "#888", fontSize: 13, margin: "8px 0" }}>
            AI가 데이터를 확인하는 중… (처음 질문은 20초쯤 걸릴 수 있어요)
          </div>
        )}
      </div>

      {err && <p style={{ color: "#b00", fontSize: 13 }}>오류: {err}</p>}

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="예: 나상원 보험료 얼마 내? (Enter 전송, Shift+Enter 줄바꿈)"
          rows={2}
          style={{ flex: 1, padding: 8, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit", fontSize: 14 }}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()} style={{ padding: "0 18px" }}>
          {busy ? "…" : "전송"}
        </button>
      </div>

      <p style={{ fontSize: 12, color: "#999", marginTop: 8 }}>
        특정 보험의 약관 내용(보장 금액·조건)은 고객 목록 → 고객 선택 → '이 고객 약관에 질문'에서 물어보세요.
      </p>
    </div>
  );
}
