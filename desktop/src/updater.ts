import type { Update } from "@tauri-apps/plugin-updater";

// 결과를 alert/confirm 으로 띄우지 않는다 — 패키지된 Tauri 앱에서 window.confirm 이
// 조용히 falsy 를 반환해 아무 일도 안 일어나던 버그(설정·진단 "업데이트 확인" 무반응)를
// 피하려고, 상태를 반환하고 App.tsx 가 화면에 렌더한다.

export type UpdateCheck =
  | { kind: "unsupported" }
  | { kind: "latest" }
  | { kind: "available"; version: string; notes?: string; update: Update }
  | { kind: "error"; message: string };

export async function checkForUpdate(): Promise<UpdateCheck> {
  if (!("__TAURI_INTERNALS__" in window)) return { kind: "unsupported" };
  try {
    const { check } = await import("@tauri-apps/plugin-updater");
    const update = await check();
    if (!update) return { kind: "latest" };
    return { kind: "available", version: update.version, notes: update.body, update };
  } catch (error) {
    console.error("업데이트 확인 실패", error);
    return { kind: "error", message: error instanceof Error ? error.message : String(error) };
  }
}

// 성공하면 앱을 재시작하므로 반환되지 않는다. 실패하면 throw.
export async function installUpdate(update: Update): Promise<never> {
  await update.downloadAndInstall();
  const { relaunch } = await import("@tauri-apps/plugin-process");
  await relaunch();
  throw new Error("relaunch 이후 도달 불가");
}
