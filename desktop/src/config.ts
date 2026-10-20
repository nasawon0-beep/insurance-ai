export let CONTROL_URL = "http://127.0.0.1:8790";
export let LOCAL_ENGINE_URL = "http://127.0.0.1:8420";
export let QA_MODE = false;
export let QA_INSTANCE_ID: string | null = null;
export let QA_PROFILE_FINGERPRINT: string | null = null;

export const IS_NATIVE_RUNTIME = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type RuntimeProfile = {
  qa: boolean;
  localEngineUrl: string;
  controlUrl: string;
  instanceId: string | null;
  profileFingerprint: string | null;
};

function validatedLoopbackUrl(value: string, name: string, qa: boolean): string {
  const url = new URL(value);
  const expectedProtocol = qa ? "https:" : "http:";
  if (url.protocol !== expectedProtocol || url.hostname !== "127.0.0.1" || url.username || url.password || url.pathname !== "/") {
    throw new Error(`${name} endpoint가 검증된 loopback ${expectedProtocol} URL이 아닙니다.`);
  }
  const port = Number(url.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${name} endpoint 포트가 올바르지 않습니다.`);
  }
  return url.origin;
}

export async function initializeRuntimeProfile(): Promise<void> {
  if (!IS_NATIVE_RUNTIME) return;
  const { invoke } = await import("@tauri-apps/api/core");
  const profile = await invoke<RuntimeProfile>("runtime_profile");
  const engineUrl = validatedLoopbackUrl(profile.localEngineUrl, "local-engine", profile.qa);
  const controlUrl = validatedLoopbackUrl(profile.controlUrl, "control-server", profile.qa);
  if (profile.qa && (!profile.instanceId || !profile.profileFingerprint)) {
    throw new Error("QA runtime identity가 없습니다.");
  }
  CONTROL_URL = controlUrl;
  LOCAL_ENGINE_URL = engineUrl;
  QA_MODE = profile.qa;
  QA_INSTANCE_ID = profile.instanceId;
  QA_PROFILE_FINGERPRINT = profile.profileFingerprint;
}

export const APP_VERSION = "2.5.3";

export const TRIAL_DAYS = 14;
export const REFRESH_INTERVAL_MS = 45_000;

export const IS_DEV = Boolean(import.meta.env?.DEV);
