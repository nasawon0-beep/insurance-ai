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
  pending?: boolean;
  response_log?: {
    route: "db_direct" | "local_llm" | "rag" | string;
    elapsed_ms: number;
    model: string;
    used_customer_id?: string | null;
    fallback: boolean;
  };
};

type AssistantTask = {
  id: string;
  question: string;
  history: { role: string; content: string }[];
  status: "pending" | "completed" | "error";
  customer_id?: string;
  placeholder_id?: string;
  result?: Message;
  error?: string;
};

type CustomerSearchResult = {
  id: string;
  name?: string | null;
};

const CUSTOMER_NAME_STOPWORDS = new Set([
  "고객",
  "보험",
  "보험료",
  "만기",
  "계약",
  "보장",
  "진단비",
  "뇌진단비",
  "암진단비",
  "얼마",
  "얼마야",
  "얼마있어",
  "언제",
  "문의",
  "이번",
  "생일",
  "후속",
  "연락",
]);

function normalizeName(value: string): string {
  return value.replace(/\s+/g, "").trim();
}

function customerNameCandidates(question: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of question.match(/[가-힣]{2,10}/g) ?? []) {
    let token = raw.replace(/(고객님|고객|님|씨|의|은|는|이|가|을|를|에게|한테|께)$/u, "");
    if (token.length < 2 || CUSTOMER_NAME_STOPWORDS.has(token)) continue;

    const candidates = [token];
    if (token.length > 4) candidates.push(token.slice(0, 4), token.slice(0, 3), token.slice(0, 2));
    else if (token.length > 3) candidates.push(token.slice(0, 3), token.slice(0, 2));

    for (const candidate of candidates) {
      if (candidate.length < 2 || CUSTOMER_NAME_STOPWORDS.has(candidate) || seen.has(candidate)) continue;
      seen.add(candidate);
      out.push(candidate);
    }
  }
  return out;
}

async function resolveCustomerIdFromQuestion(question: string): Promise<string | undefined> {
  for (const candidate of customerNameCandidates(question)) {
    const res = await api(`/customers?q=${encodeURIComponent(candidate)}&limit=5`);
    const customers = Array.isArray(res?.customers) ? (res.customers as CustomerSearchResult[]) : [];
    if (!customers.length) continue;

    const exact = customers.find((c) => normalizeName(c.name ?? "") === normalizeName(candidate));
    if (exact?.id) return exact.id;

    const contained = customers.find((c) => {
      const name = normalizeName(c.name ?? "");
      return name.length >= 2 && (question.includes(name) || name.includes(candidate));
    });
    if (contained?.id) return contained.id;

    if (customers.length === 1 && customers[0].id) return customers[0].id;
  }
  return undefined;
}

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
          const customerId = task.customer_id ?? (await resolveCustomerIdFromQuestion(task.question).catch(() => undefined));
          const payload: { question: string; history: { role: string; content: string }[]; customer_id?: string } = {
            question: task.question,
            history: task.history,
          };
          if (customerId) payload.customer_id = customerId;

          const res = await api("/assistant/ask", {
            method: "POST",
            body: JSON.stringify(payload),
          });

          const assistantMessage: Message = {
            role: "assistant",
            content: res.answer,
            used_customers: res.used_customers ?? [],
            no_data: !!res.no_data,
            response_log: res.response_log,
          };

          // 작업 완료
          setTasks((prev) =>
            prev.map((t) =>
              t.id === task.id ? { ...t, status: "completed" as const, result: assistantMessage } : t
            )
          );

          // 진행 표시 메시지를 실제 답변으로 교체 (탭 이동 중에도 유지)
          setMessages((prev) =>
            prev.map((m) =>
              task.placeholder_id && m.pending && (m as Message & { id?: string }).id === task.placeholder_id
                ? assistantMessage
                : m
            )
          );

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
          setMessages((prev) =>
            prev.map((m) =>
              task.placeholder_id && m.pending && (m as Message & { id?: string }).id === task.placeholder_id
                ? { role: "assistant", content: `오류: ${errorMessage}`, no_data: true }
                : m
            )
          );
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
    
    const taskId = `${Date.now()}-${Math.random()}`;
    const placeholderId = `progress-${taskId}`;

    // 사용자 메시지와 진행 표시를 즉시 추가
    setMessages((prev) => [
      ...prev,
      { role: "user", content: trimmedQuestion },
      { role: "assistant", content: "고객 정보 확인 중...", pending: true, id: placeholderId } as Message & { id: string },
    ]);
    setErr(null);
    setBusy(true);

    // 알림 권한 요청 (처음 한 번만)
    if ("Notification" in window && Notification.permission === "default") {
      await Notification.requestPermission();
    }

    // 백그라운드 작업 큐에 추가
    setTasks((prev) => [
      ...prev,
      {
        id: taskId,
        placeholder_id: placeholderId,
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
