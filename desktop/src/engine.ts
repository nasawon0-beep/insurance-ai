import { LOCAL_ENGINE_URL } from "./config";
import { invoke } from "@tauri-apps/api/core";

export { LOCAL_ENGINE_URL };

const API_SECRET_HEADER = "X-Insurance-AI-Secret";
const ACTOR_HEADER = "X-Actor-Id";
const ENGINE_TIMEOUT_MS = 5000;
const LONG_RUNNING_ENGINE_TIMEOUT_MS = 180000;
const STARTUP_RECOVERY_RETRY_DELAYS_MS = [250, 500, 1000, 1500, 2500];
const LONG_RUNNING_PATH_PREFIXES = [
  "/capture",
  "/parse/pdf",
  "/import/preview",
  "/import/commit",
];
let secretPromise: Promise<string> | null = null;
let recoveryPromise: Promise<void> | null = null;
let readinessPromise: Promise<void> | null = null;

type NativeEngineRequestBody =
  | { type: "text"; text: string }
  | { type: "bytes"; bytes: number[] }
  | { type: "formData"; fields: NativeEngineFormField[] };

type NativeEngineFormField = {
  name: string;
  value?: string;
  file_name?: string;
  content_type?: string;
  bytes?: number[];
};

type NativeEngineResponse = {
  status: number;
  body: number[];
  headers: [string, string][];
};

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function getActorId(): string | null {
  try {
    const id = JSON.parse(localStorage.getItem("iai.user") || "null")?.id;
    return typeof id === "string" && id ? id : null;
  } catch {
    return null;
  }
}

function isLongRunningEnginePath(path: string): boolean {
  return LONG_RUNNING_PATH_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function timeoutForEnginePath(path: string): number {
  return isLongRunningEnginePath(path) ? LONG_RUNNING_ENGINE_TIMEOUT_MS : ENGINE_TIMEOUT_MS;
}

function methodForInit(init: RequestInit = {}): string {
  return (init.method || "GET").toUpperCase();
}

function canRetryEngineStartupRequest(path: string, init: RequestInit = {}): boolean {
  const method = methodForInit(init);
  return !isLongRunningEnginePath(path) && (method === "GET" || method === "HEAD");
}

function isLocalEngineStartupError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /connect|connection|refused|ECONNREFUSED|Failed to fetch|NetworkError|Load failed|AbortError|timed out|api-secret|인증 파일|시간|거부|연결/i.test(message);
}

async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = ENGINE_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: init.signal ?? controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

function bytesFromBuffer(buffer: ArrayBuffer): number[] {
  return Array.from(new Uint8Array(buffer));
}

async function nativeBodyFromInit(init: RequestInit): Promise<NativeEngineRequestBody | null> {
  const body = init.body;
  if (body == null) return null;
  if (typeof body === "string") return { type: "text", text: body };
  if (body instanceof URLSearchParams) return { type: "text", text: body.toString() };
  if (body instanceof FormData) {
    const fields: NativeEngineFormField[] = [];
    for (const [name, value] of body.entries()) {
      if (value instanceof File) {
        fields.push({
          name,
          file_name: value.name,
          content_type: value.type || undefined,
          bytes: bytesFromBuffer(await value.arrayBuffer()),
        });
      } else {
        fields.push({ name, value: String(value) });
      }
    }
    return { type: "formData", fields };
  }
  if (body instanceof Blob) return { type: "bytes", bytes: bytesFromBuffer(await body.arrayBuffer()) };
  if (body instanceof ArrayBuffer) return { type: "bytes", bytes: bytesFromBuffer(body) };
  if (ArrayBuffer.isView(body)) {
    const view = body as ArrayBufferView;
    return { type: "bytes", bytes: Array.from(new Uint8Array(view.buffer, view.byteOffset, view.byteLength)) };
  }
  throw new Error("local-engine native fallback은 이 요청 본문 형식을 지원하지 않습니다");
}

async function nativeEngineFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  // reqwest가 multipart boundary를 새로 만들기 때문에 기존 browser boundary 헤더는 제거한다.
  if (init.body instanceof FormData) headers.delete("Content-Type");
  const result = await invoke<NativeEngineResponse>("local_engine_request", {
    request: {
      method: init.method || "GET",
      path,
      headers: Array.from(headers.entries()),
      body: await nativeBodyFromInit(init),
    },
  });
  return new Response(new Uint8Array(result.body), {
    status: result.status,
    headers: result.headers,
  });
}

export async function ensureLocalEngineRecovered(): Promise<void> {
  if (!recoveryPromise) {
    recoveryPromise = invoke<void>("ensure_local_engine")
      .then(() => {
        secretPromise = null;
        readinessPromise = null;
      })
      .finally(() => {
        recoveryPromise = null;
      });
  }
  return recoveryPromise;
}

async function waitForLocalEngineReady(): Promise<void> {
  if (!readinessPromise) {
    readinessPromise = (async () => {
      let lastError: unknown = null;
      for (let attempt = 0; attempt <= STARTUP_RECOVERY_RETRY_DELAYS_MS.length; attempt += 1) {
        try {
          const response = await browserThenNativeEngineFetch("/health", {});
          if (response.ok) {
            const body = await response.json().catch(() => null);
            if (body?.local_engine === "ok") return;
            lastError = new Error(`local-engine 상태: ${body?.local_engine ?? "unknown"}`);
          } else {
            lastError = new Error(`local-engine health HTTP ${response.status}`);
          }
        } catch (error) {
          lastError = error;
        }
        if (attempt < STARTUP_RECOVERY_RETRY_DELAYS_MS.length) await wait(STARTUP_RECOVERY_RETRY_DELAYS_MS[attempt]);
      }
      throw lastError instanceof Error ? lastError : new Error(String(lastError ?? "local-engine 준비 실패"));
    })().finally(() => {
      readinessPromise = null;
    });
  }
  return readinessPromise;
}

async function getApiSecret(): Promise<string> {
  if (!secretPromise) {
    secretPromise = fetchWithTimeout(`${LOCAL_ENGINE_URL}/api-secret`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`Engine authentication failed: HTTP ${res.status}`);
        const body = await res.json();
        if (typeof body.secret !== "string" || !body.secret)
          throw new Error("Engine authentication failed: missing secret");
        return body.secret;
      })
      .catch(async (error) => {
        console.warn("local-engine /api-secret fetch failed; trying Tauri secret bridge", error);
        const bridged = await invoke<string>("local_engine_api_secret");
        if (!bridged) throw new Error("Engine authentication failed: missing bridged secret");
        return bridged;
      })
      .catch((error) => {
        secretPromise = null;
        throw error;
      });
  }
  return secretPromise;
}

async function getApiSecretAfterStartupWait(): Promise<string> {
  try {
    return await getApiSecret();
  } catch (error) {
    if (!isLocalEngineStartupError(error)) throw error;
    await waitForLocalEngineReady();
    secretPromise = null;
    return getApiSecret();
  }
}

async function browserEngineFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, init, timeoutForEnginePath(path));
}

async function browserThenNativeEngineFetch(path: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await browserEngineFetch(path, init);
  } catch (error) {
    console.warn("local-engine browser fetch failed; trying Tauri native bridge", { path, error });
    return nativeEngineFetch(path, init);
  }
}

async function engineFetchOnce(path: string, init: RequestInit = {}, retryWithFreshSecret = true): Promise<Response> {
  if (path === "/health") {
    try {
      return await fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, init, 1000);
    } catch (error) {
      console.warn("local-engine /health browser fetch failed; trying Tauri native bridge", error);
      return nativeEngineFetch(path, init);
    }
  }

  const headers = new Headers(init.headers);
  headers.set(API_SECRET_HEADER, await getApiSecretAfterStartupWait());
  const actorId = getActorId();
  if (actorId) headers.set(ACTOR_HEADER, actorId);

  const response = await browserThenNativeEngineFetch(path, { ...init, headers });
  if (response.status === 401 && retryWithFreshSecret) {
    secretPromise = null;
    return engineFetchOnce(path, init, false);
  }
  return response;
}

export async function engineFetch(
  path: string,
  init: RequestInit = {},
  retryWithFreshSecret = true,
): Promise<Response> {
  try {
    return await engineFetchOnce(path, init, retryWithFreshSecret);
  } catch (error) {
    if (!retryWithFreshSecret || !canRetryEngineStartupRequest(path, init) || !isLocalEngineStartupError(error)) {
      throw error;
    }
    console.warn("local-engine request failed before startup completed; waiting for readiness and retrying", { path, error });
    await waitForLocalEngineReady();
    return engineFetchOnce(path, init, retryWithFreshSecret);
  }
}
