export async function checkForUpdate(opts?: { silent?: boolean }): Promise<void> {
  const silent = opts?.silent ?? false;

  if (!("__TAURI_INTERNALS__" in window)) return;

  let check: typeof import("@tauri-apps/plugin-updater")["check"];
  try {
    ({ check } = await import("@tauri-apps/plugin-updater"));
  } catch {
    return;
  }

  try {
    const update = await check();

    if (!update) {
      if (!silent) window.alert("최신 버전입니다");
      return;
    }

    if (!window.confirm(`새 버전 ${update.version} 이 있습니다. 지금 설치할까요?`)) return;

    await update.downloadAndInstall();
    const { relaunch } = await import("@tauri-apps/plugin-process");
    await relaunch();
  } catch (error) {
    console.error("업데이트 확인 또는 설치에 실패했습니다.", error);
    if (!silent) window.alert("업데이트 확인 또는 설치 중 오류가 발생했습니다.");
  }
}
