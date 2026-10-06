import { LOCAL_ENGINE_URL } from "./config";
import { invoke } from "@tauri-apps/api/core";

export { LOCAL_ENGINE_URL };

const API_SECRET_HEADER = "X-Insurance-AI-Secret";
const ACTOR_HEADER = "X-Actor-Id";
const ENGINE_TIMEOUT_MS = 5000;
const LONG_RUNNING_ENGINE_TIMEOUT_MS = 180000;
const LONG_RUNNING_PATH_PREFIXES = [
  "/capture",
  "/parse/pdf",
  "/import/preview",
  "/import/commit",
];
let secretPromise: Promise<string> | null = null;
let recoveryPromise: Promise<void> | null = null;

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

async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = ENGINE_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: init.signal ?? controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

export async function ensureLocalEngineRecovered(): Promise<void> {
  if (!recoveryPromise) {
    recoveryPromise = invoke<void>("ensure_local_engine")
      .then(() => {
        secretPromise = null;
      })
      .finally(() => {
        recoveryPromise = null;
      });
  }
  return recoveryPromise;
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

export async function engineFetch(
  path: string,
  init: RequestInit = {},
  retryWithFreshSecret = true,
): Promise<Response> {
  if (path === "/health") return fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, init, 1000);

  const headers = new Headers(init.headers);
  headers.set(API_SECRET_HEADER, await getApiSecret());
  const actorId = getActorId();
  if (actorId) headers.set(ACTOR_HEADER, actorId);
  try {
    const response = await fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, { ...init, headers }, timeoutForEnginePath(path));
    if (response.status === 401 && retryWithFreshSecret) {
      secretPromise = null;
      return engineFetch(path, init, false);
    }
    return response;
  } catch (error) {
    // Do not auto-restart local-engine from ordinary request failures.
    // Restarting during uploads/analysis makes the UI stutter and can kill in-flight work.
    // The user can still use the explicit "로컬 엔진 재시작" button.
    throw error;
  }
}
