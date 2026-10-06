import { LOCAL_ENGINE_URL } from "./config";
import { invoke } from "@tauri-apps/api/core";

export { LOCAL_ENGINE_URL };

const API_SECRET_HEADER = "X-Insurance-AI-Secret";
const ACTOR_HEADER = "X-Actor-Id";
const ENGINE_TIMEOUT_MS = 5000;
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

function isNetworkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /Failed to fetch|NetworkError|Load failed|abort|timed?\s*out/i.test(message);
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
        if (isNetworkError(error)) {
          console.warn("local-engine /api-secret fetch failed; asking Tauri to recover local-engine", error);
          await ensureLocalEngineRecovered();
          const res = await fetchWithTimeout(`${LOCAL_ENGINE_URL}/api-secret`);
          if (!res.ok) throw new Error(`Engine authentication failed after recovery: HTTP ${res.status}`);
          const body = await res.json();
          if (typeof body.secret === "string" && body.secret) return body.secret;
        }
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
  if (path === "/health") return fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, init, 2500);

  const headers = new Headers(init.headers);
  headers.set(API_SECRET_HEADER, await getApiSecret());
  const actorId = getActorId();
  if (actorId) headers.set(ACTOR_HEADER, actorId);
  try {
    const response = await fetchWithTimeout(`${LOCAL_ENGINE_URL}${path}`, { ...init, headers });
    if (response.status === 401 && retryWithFreshSecret) {
      secretPromise = null;
      return engineFetch(path, init, false);
    }
    return response;
  } catch (error) {
    if (retryWithFreshSecret && isNetworkError(error)) {
      await ensureLocalEngineRecovered();
      return engineFetch(path, init, false);
    }
    throw error;
  }
}
