// 모바일 연결 패널의 순수 정책(화면 없이 node --test로 검증).
/** 표시만 대체한다. 원본 기기 이름이나 등록 정보는 변경하지 않는다. */
export function mobileDeviceDisplayName(name: string): string {
  const text = name.trim();
  return !text || /[\uFFFD\u0000-\u001F\u007F-\u009F]|\?{2,}/.test(text)
    ? "Android 휴대폰" : text;
}
/** 시작 중에는 짧게, 실행/중지 상태에서는 길게 다시 읽는다(설정 화면은 항상 마운트돼 있어 1회 조회로는 갱신되지 않음). */
export function mobileStatusPollMs(status: { starting: boolean }, visible: boolean): number | null {
  if (!visible) return null;
  return status.starting ? 1500 : 10000;
}

/** QR을 화면에서 내려야 하는 이유. null이면 계속 표시한다. */
export function pairingQrCloseReason(
  qr: { expiresAt: number; url: string },
  status: { running: boolean; url: string | null },
  nowSeconds: number,
  activeDevicesAtIssue: number,
  activeDevicesNow: number,
): string | null {
  if (nowSeconds >= qr.expiresAt) return "QR이 만료되었습니다. 필요하면 새 QR을 만드세요.";
  if (!status.running) return "모바일 연결이 중지되어 QR을 닫았습니다.";
  if (status.url !== qr.url) return "PC 주소가 바뀌어 QR을 닫았습니다. 새 QR을 만드세요.";
  if (activeDevicesNow > activeDevicesAtIssue) return "새 모바일이 등록되었습니다. QR은 더 이상 필요하지 않습니다.";
  return null;
}
