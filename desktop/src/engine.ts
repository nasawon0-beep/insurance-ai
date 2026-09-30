import { LOCAL_ENGINE_URL } from "./config";
import { invoke } from "@tauri-apps/api/core";

export { LOCAL_ENGINE_URL };

const API_SECRET_HEADER = "X-Insurance-AI-Secret";
const ACTOR_HEADER = "X-Actor-Id";
let secretPromise: Promise<string> | null = null;

function getActorId(): string | null {
  try {
    const id = JSON.parse(localStorage.getItem("iai.user") || "null")?.id;
    return typeof id === "string" && id ? id : null;
  } catch {
    return null;
  }
}

async function getApiSecret(): Promise<string> {
  if (!secretPromise) {
    secretPromise = fetch(`${LOCAL_ENGINE_URL}/api-secret`)
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
  if (path === "/health") return fetch(`${LOCAL_ENGINE_URL}${path}`, init);

  const headers = new Headers(init.headers);
  headers.set(API_SECRET_HEADER, await getApiSecret());
  const actorId = getActorId();
  if (actorId) headers.set(ACTOR_HEADER, actorId);
  const response = await fetch(`${LOCAL_ENGINE_URL}${path}`, { ...init, headers });
  if (response.status === 401 && retryWithFreshSecret) {
    secretPromise = null;
    return engineFetch(path, init, false);
  }
  return response;
}
