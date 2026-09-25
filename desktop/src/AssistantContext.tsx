import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from "react";
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
export type Message = {
  role: "user" | "assistant";
  content: string;
  used_customers?: UsedCustomer[];
  no_data?: boolean;
};

type AssistantTask = {
  id: string;
  question: string;
  history: { role: string; content: string }[];
  status: "pending" | "completed" | "error";
  result?: Message;
  error?: string;
};

type AssistantContextType = {
  messages: Message[];
  setMessages: (messages: Message[]) => void;
  busy: boolean;
  err: string | null;
  setErr: (err: string | null) => void;
  pendingTasks: number;
  sendQuestion: (question: string, history: Message[]) => Promise<void>;
  clearMessages: () => void;
};

const AssistantContext = createContext<AssistantContextType | undefined>(undefined);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tasks, setTasks] = useState<AssistantTask[]>([]);

  const pendingTasks = tasks.filter((t) => t.status === "pending").length;

  // 백그라운드 작업 처리
  useEffect(() => {
    const processTasks = async () => {
      for (const task of tasks) {
        if (task.status !== "pending") continue;

        try {
          const res = await api("/assistant/ask", {
            method: "POST",
            body: JSON.stringify({ question: task.question, history: task.history }),
          });

          const assistantMessage: Message = {
            role: "assistant",
            content: res.answer,
            used_customers: res.used_customers ?? [],
            no_data: !!res.no_data,
          };

          // 작업 완료
          setTasks((prev) =>
            prev.map((t) =>
              t.id === task.id ? { ...t, status: "completed" as const, result: assistantMessage } : t
            )
          );

          // 메시지 추가
          setMessages((prev) => [...prev, assistantMessage]);

          // 알림 표시
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification("AI 응답 완료!", {
              body: "AI 문의 탭에서 답변을 확인하세요",
              icon: "/icon.png",
              tag: "assistant-response",
            });
          }

          setBusy(false);
        } catch (e) {
          const errorMessage = e instanceof Error ? e.message : String(e);
          
          setTasks((prev) =>
            prev.map((t) => (t.id === task.id ? { ...t, status: "error" as const, error: errorMessage } : t))
          );
          setErr(errorMessage);
          setBusy(false);
        }
      }
    };

    if (pendingTasks > 0) {
      processTasks();
    }
  }, [tasks, pendingTasks]);

  const sendQuestion = useCallback(async (question: string, history: Message[]) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) return;

    const historyForAPI = history.slice(-6).map((m) => ({ role: m.role, content: m.content }));
    
    // 사용자 메시지 즉시 추가
    setMessages((prev) => [...prev, { role: "user", content: trimmedQuestion }]);
    setErr(null);
    setBusy(true);

    // 알림 권한 요청 (처음 한 번만)
    if ("Notification" in window && Notification.permission === "default") {
      await Notification.requestPermission();
    }

    // 백그라운드 작업 큐에 추가
    const taskId = `${Date.now()}-${Math.random()}`;
    setTasks((prev) => [
      ...prev,
      {
        id: taskId,
        question: trimmedQuestion,
        history: historyForAPI,
        status: "pending",
      },
    ]);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setTasks([]);
    setErr(null);
    setBusy(false);
  }, []);

  return (
    <AssistantContext.Provider
      value={{
        messages,
        setMessages,
        busy,
        err,
        setErr,
        pendingTasks,
        sendQuestion,
        clearMessages,
      }}
    >
      {children}
    </AssistantContext.Provider>
  );
}

export function useAssistant() {
  const context = useContext(AssistantContext);
  if (!context) {
    throw new Error("useAssistant must be used within AssistantProvider");
  }
  return context;
}
