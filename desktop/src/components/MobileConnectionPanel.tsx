import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { mobileStatusPollMs, pairingQrCloseReason } from "./mobileSyncPolicy.ts";
import { createFirewallPoller, firewallApplyError, firewallQrAllowed, type MobileFirewallStatus } from "./mobileFirewallPolicy.ts";

type MobileSyncStatus = {
  running: boolean;
  starting: boolean;
  url: string | null;
  host: string | null;
  port: number;
  certificateFingerprint: string | null;
  certificateExpiresAt: number | null;
  lastError: string | null;
};

type PairingCode = { code: string; expiresAt: number };
type PairingQr = {
  svg: string;
  code: string;
  expiresAt: number;
  payloadVersion: number;
  url: string;
  certificateFingerprint: string;
};
type MobileDevice = { id: string; name: string; created_at: number; revoked_at: number | null };
type MobileStartupPreference = { restoreOnAppStart: boolean };

const emptyStatus: MobileSyncStatus = {
  running: false,
  starting: false,
  url: null,
  host: null,
  port: 18443,
  certificateFingerprint: null,
  certificateExpiresAt: null,
  lastError: null,
};

function formattedTime(timestamp: number | null): string {
  if (!timestamp) return "-";
  return new Date(timestamp * 1000).toLocaleString();
}

export default function MobileConnectionPanel({ visible = true }: { visible?: boolean }) {
  const [status, setStatus] = useState<MobileSyncStatus>(emptyStatus);
  const [devices, setDevices] = useState<MobileDevice[]>([]);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [recovery, setRecovery] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [restoreOnAppStart, setRestoreOnAppStart] = useState(false);
  const [qr, setQr] = useState<(PairingQr & { activeAtIssue: number }) | null>(null);
  const [nowSeconds, setNowSeconds] = useState(() => Math.floor(Date.now() / 1000));
  const [firewallStatus, setFirewallStatus] = useState<MobileFirewallStatus | null>(null);
  const [firewallError, setFirewallError] = useState<string | null>(null);
  const [firewallBusy, setFirewallBusy] = useState(false);
  const firewallPoller = useRef<ReturnType<typeof createFirewallPoller> | null>(null);
  const qrAllowed = visible && firewallQrAllowed(status.running, firewallStatus) && !firewallBusy;
  const pairingGeneration = useRef(0);
  const invalidatePairing = useCallback(() => {
    pairingGeneration.current++;
    setQr(null);
    setPairing(null);
    setRecovery(null);
  }, []);

  // Reject pending credentials even if readiness returns before they resolve.
  // Layout cleanup also fences unmount and context changes before painting.
  useLayoutEffect(() => {
    invalidatePairing();
    return () => { pairingGeneration.current++; };
  }, [visible, status.host, status.url, status.certificateFingerprint, status.running, qrAllowed, invalidatePairing]);

  // PowerShell is independent of the fast server poll. Never elevate on a timer.
  useEffect(() => {
    setFirewallStatus(null);
    if (!visible || firewallBusy) return;
    const poller = createFirewallPoller(
      () => invoke<MobileFirewallStatus>("mobile_sync_firewall_status"),
      (next) => { setFirewallStatus(next); setFirewallError(next.lastError); },
      (message) => { setFirewallStatus(null); setFirewallError(message); },
    );
    firewallPoller.current = poller;
    const networkRefresh = () => { invalidatePairing(); setFirewallStatus(null); poller.refresh(true); };
    poller.refresh();
    const timer = window.setInterval(() => poller.refresh(), 15000);
    window.addEventListener("online", networkRefresh);
    window.addEventListener("offline", networkRefresh);
    window.addEventListener("focus", networkRefresh);
    return () => {
      poller.dispose();
      firewallPoller.current = null;
      window.clearInterval(timer);
      window.removeEventListener("online", networkRefresh);
      window.removeEventListener("offline", networkRefresh);
      window.removeEventListener("focus", networkRefresh);
    };
  }, [visible, status.host, status.running, firewallBusy, invalidatePairing]);

  const refresh = useCallback(async () => {
    const next = await invoke<MobileSyncStatus>("mobile_sync_status");
    setStatus(next);
    const preference = await invoke<MobileStartupPreference>("mobile_sync_startup_preference");
    setRestoreOnAppStart(preference.restoreOnAppStart);
    try {
      setDevices(await invoke<MobileDevice[]>("mobile_sync_list_devices"));
    } catch {
      setDevices([]);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    void refresh().catch((reason) => setError(String(reason)));
  }, [refresh, visible]);

  // 앱 시작 직후 자동 복원 중(starting)에 읽은 상태가 고정되지 않도록 화면이 보일 때 주기적으로 상태만 다시 읽는다.
  const pollMs = mobileStatusPollMs(status, visible);
  useEffect(() => {
    if (pollMs === null) return;
    const timer = window.setInterval(() => {
      void invoke<MobileSyncStatus>("mobile_sync_status").then(setStatus).catch(() => undefined);
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs]);

  // QR 표시 중: 1초마다 만료 확인, 4초마다 새 기기 등록 여부 확인.
  useEffect(() => {
    if (!qr) return;
    const tick = window.setInterval(() => setNowSeconds(Math.floor(Date.now() / 1000)), 1000);
    const devicesTimer = window.setInterval(() => {
      void invoke<MobileDevice[]>("mobile_sync_list_devices").then(setDevices).catch(() => undefined);
      void invoke<MobileSyncStatus>("mobile_sync_status").then(setStatus).catch(() => undefined);
    }, 4000);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(devicesTimer);
    };
  }, [qr]);

  const activeDevices = devices.filter((device) => !device.revoked_at).length;
  useEffect(() => {
    if (!qr) return;
    const reason = pairingQrCloseReason(qr, status, nowSeconds, qr.activeAtIssue, activeDevices);
    if (reason) {
      setQr(null);
      setNotice(reason);
    }
  }, [qr, status, nowSeconds, activeDevices]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const start = () => run(async () => {
    setStatus(await invoke<MobileSyncStatus>("mobile_sync_start"));
    setNotice("모바일 연결 서버를 시작했습니다. 방화벽 허용은 별도로 승인해야 합니다.");
  });
  const stop = () => run(async () => {
    setStatus(await invoke<MobileSyncStatus>("mobile_sync_stop"));
    setRestoreOnAppStart(false);
    setPairing(null);
    setQr(null);
    setNotice("모바일 연결 서버를 중지했습니다.");
  });
  const createCode = () => run(async () => {
    if (!qrAllowed) throw new Error("Private 네트워크 방화벽 설정을 먼저 확인하세요.");
    const generation = pairingGeneration.current;
    const next = await invoke<PairingCode>("mobile_sync_create_pairing_code");
    if (generation !== pairingGeneration.current) return;
    setPairing(next);
    setRecovery(null);
    setQr(null);
  });
  const createQr = () => run(async () => {
    if (!qrAllowed) throw new Error("Private 네트워크 방화벽 설정을 먼저 확인하세요.");
    const generation = pairingGeneration.current;
    const next = await invoke<PairingQr>("mobile_sync_create_pairing_qr");
    if (generation !== pairingGeneration.current) return;
    setNowSeconds(Math.floor(Date.now() / 1000));
    setQr({ ...next, activeAtIssue: activeDevices });
    setPairing(null);
    setRecovery(null);
  });
  const closeQr = () => {
    setQr(null);
    setNotice("QR을 닫았습니다. 이미 만든 코드는 5분 안에 한 번만 쓸 수 있고 그 뒤 자동으로 만료됩니다.");
  };
  const createRecoveryCode = (device: MobileDevice) => run(async () => {
    if (!qrAllowed) throw new Error("Private 네트워크 방화벽 설정을 먼저 확인하세요.");
    if (!window.confirm(`‘${device.name}’ 기기를 기존 등록 정보와 미전송 자료를 유지한 채 다시 연결할까요?`)) return;
    const generation = pairingGeneration.current;
    const next = await invoke<PairingCode>("mobile_sync_create_recovery_code", { deviceId: device.id, confirmed: true });
    if (generation !== pairingGeneration.current) return;
    setRecovery(next);
    setPairing(null);
    setNotice("선택한 기기 전용 복구 코드를 만들었습니다. 모바일에서 ‘기존 연결 복구’에 입력하세요.");
  });
  const setStartupRestore = (enabled: boolean) => run(async () => {
    const preference = await invoke<MobileStartupPreference>("mobile_sync_set_startup_preference", { enabled });
    setRestoreOnAppStart(preference.restoreOnAppStart);
    setNotice(enabled ? "다음부터 로그인 후 모바일 연결을 자동으로 복원합니다." : "앱 실행 시 자동 복원을 사용하지 않습니다.");
  });
  const revoke = (device: MobileDevice) => run(async () => {
    if (!window.confirm(`‘${device.name}’ 기기의 모바일 연결을 해제할까요?`)) return;
    await invoke("mobile_sync_revoke_device", { deviceId: device.id });
    setNotice("기기 연결을 해제했습니다. 기존 인증 정보로는 더 이상 동기화할 수 없습니다.");
  });
  const firewall = (enable: boolean) => run(async () => {
    if (!firewallStatus?.supported || firewallStatus.qa || (enable && (!status.running || !firewallStatus.canApply))) {
      throw new Error("현재 환경에서는 방화벽 변경을 승인할 수 없습니다. QA에서는 실제 규칙을 변경하지 않습니다.");
    }
    const message = enable
      ? "Windows 관리자 승인(UAC)을 요청할까요? 설치된 local-engine.exe의 TCP 18443만 Private / LocalSubnet(같은 서브넷)에 허용합니다. Public·회사 Domain 프로필과 네트워크 종류는 변경하지 않습니다. 휴대폰 연결 성공을 보장하지 않습니다."
      : "모아둠 모바일 연결용 Windows 방화벽 규칙을 비활성화할까요? 규칙을 삭제하지 않습니다. 관리자 승인(UAC)이 필요합니다.";
    if (!window.confirm(message)) return;
    firewallPoller.current?.dispose();
    setFirewallBusy(true);
    setFirewallStatus(null);
    setFirewallError(null);
    try {
      const result = await invoke<MobileFirewallStatus>("mobile_sync_firewall_apply", { enable, confirmed: true });
      setFirewallStatus(result);
      const failure = firewallApplyError(result, enable);
      if (failure) throw new Error(failure);
      setNotice(enable ? "Private / LocalSubnet 방화벽 설정을 확인했습니다. 이제 QR로 연결하세요. 실제 휴대폰 연결은 아직 확인되지 않았습니다." : "모바일 연결 방화벽 규칙을 비활성화했습니다(삭제 아님).");
    } finally {
      setFirewallBusy(false);
    }
  });

  const row = (label: string, value: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: "0.875rem", padding: "5px 0" }}>
      <span style={{ minWidth: 150, color: "var(--color-text-secondary)" }}>{label}</span>
      <span style={{ overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );

  return (
    <div style={{ border: "1px solid var(--color-border-default)", borderRadius: 8, padding: 12, marginBottom: 12, background: "var(--color-bg-surface)" }}>
      <div style={{ marginBottom: 8 }}><b>모바일 연결</b></div>
      <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", margin: "0 0 8px" }}>
        본인의 모바일과 이 PC를 같은 Wi-Fi 또는 핫스팟에서만 연결합니다. 팀 공유·인터넷 원격 동기화 기능이 아닙니다.
      </p>
      <p style={{ fontSize: "0.875rem" }}>1. 연결 시작 → 2. 방화벽 승인·설정 확인 → 3. QR로 폰 연결</p>
      {row("서버 상태", status.running ? "실행 중 (휴대폰 연결 확인 아님)" : status.starting ? "시작 중…" : "중지됨")}
      {row("네트워크 종류", firewallStatus?.networkCategory ?? "확인 중 / 알 수 없음")}
      {row("방화벽 설정", firewallBusy ? "관리자 승인·설정 확인 중…" : firewallQrAllowed(true, firewallStatus) ? "Private / LocalSubnet 규칙 확인됨" : "미확인 또는 허용되지 않음")}
      {row("휴대폰 연결", "아직 확인되지 않음 — 서버 실행·방화벽 규칙만으로 실제 연결을 판단하지 않습니다.")}
      {firewallStatus?.qa && <p role="status">QA 모드: 실제 방화벽 변경은 금지됩니다. 읽기 전용 상태/테스트 fixture로 화면을 확인합니다.</p>}
      <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
        Public·Domain 네트워크를 강제로 Private로 바꾸지 않습니다. 신뢰할 수 있는 개인 Wi-Fi/핫스팟에서만 진행하세요.
        게스트 Wi-Fi·회사망은 기기 간 통신이 격리되어 같은 Wi-Fi여도 연결되지 않을 수 있습니다.
      </p>
      <details style={{ fontSize: "0.75rem", marginBottom: 8 }}>
        <summary>연결 시간 초과 / QR을 찍어도 연결되지 않나요?</summary>
        <p>폰과 PC의 Wi-Fi/핫스팟, PC 주소, Private 네트워크와 방화벽 상태를 확인하세요. 게스트·회사망 격리, VPN, 보안 프로그램이 통신을 막을 수 있습니다. 신뢰할 수 있는 개인 핫스팟에서 다시 시도하거나 관리자에게 문의하세요. 방화벽 전체를 끄지 마세요. QR 만료 시 새 QR을 만드세요.</p>
        {row("규칙 상태", firewallStatus?.ruleState ?? "unknown")}
        {row("프로그램", firewallStatus?.programPath ?? "-")}
        {row("규칙 이름", firewallStatus?.ruleName ?? "-")}
      </details>
      {row("PC 주소", status.url ?? "연결을 시작하면 표시됩니다")}
      {row("인증서 지문", status.certificateFingerprint ?? "-")}
      {row("인증서 만료", formattedTime(status.certificateExpiresAt))}
      <label style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 44, fontSize: "0.875rem" }}>
        <input
          type="checkbox"
          checked={restoreOnAppStart}
          disabled={busy}
          onChange={(event) => void setStartupRestore(event.currentTarget.checked)}
        />
        앱 실행 후 로그인하면 모바일 연결 자동 복원
      </label>
      <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", margin: "0 0 8px" }}>
        Windows 부팅 서비스는 만들지 않습니다. ‘연결 중지’를 누르면 자동 복원도 함께 꺼집니다.
      </p>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
        <button disabled={busy || status.running || status.starting} onClick={() => void start()}>연결 시작</button>
        <button disabled={busy || !status.running} onClick={() => void stop()}>연결 중지</button>
        <button disabled={busy || !status.running || !firewallStatus?.canApply || firewallStatus?.qa} onClick={() => void firewall(true)}>Private 방화벽 승인 (UAC)</button>
        <button disabled={busy || !qrAllowed} onClick={() => void createQr()}>QR로 폰 연결</button>
        <button disabled={busy || !qrAllowed} onClick={() => void createCode()}>새 기기 등록 코드</button>
        <button disabled={busy || !firewallStatus?.supported || firewallStatus?.qa} onClick={() => void firewall(false)}>방화벽 규칙 비활성화</button>
        <button disabled={firewallBusy} onClick={() => firewallPoller.current?.refresh()}>방화벽 상태 새로고침</button>
      </div>
      {qr && qrAllowed && (
        <div role="status" style={{ marginTop: 10, padding: 10, border: "1px solid var(--color-border-default)", borderRadius: 6, background: "#fff" }}>
          <div style={{ fontSize: "0.875rem", marginBottom: 6 }}><b>폰의 모아둠 앱 → PC 동기화 → ‘PC QR 스캔’으로 찍어 주세요</b></div>
          <img
            alt="모바일 연결 QR"
            src={`data:image/svg+xml;base64,${btoa(qr.svg)}`}
            style={{ width: 240, height: 240, display: "block", imageRendering: "pixelated" }}
          />
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
            남은 시간 {Math.max(0, qr.expiresAt - nowSeconds)}초 · 1회용 · 이 PC 주소와 인증서 지문만 담겨 있습니다.
          </div>
          <details style={{ fontSize: "0.75rem", marginTop: 6 }}>
            <summary>카메라를 쓸 수 없을 때(직접 입력)</summary>
            <div>주소: {qr.url}</div>
            <div style={{ overflowWrap: "anywhere" }}>인증서 SHA-256: {qr.certificateFingerprint}</div>
            <div>1회용 코드: <strong style={{ letterSpacing: 3 }}>{qr.code}</strong></div>
          </details>
          <button style={{ marginTop: 6 }} onClick={closeQr}>QR 닫기</button>
        </div>
      )}
      {pairing && qrAllowed && (
        <div role="status" style={{ marginTop: 10, padding: 10, border: "1px solid var(--color-border-default)", borderRadius: 6 }}>
          <div>1회용 연결 코드: <strong style={{ fontSize: "1.25rem", letterSpacing: 3 }}>{pairing.code}</strong></div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>만료: {formattedTime(pairing.expiresAt)}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>이 값은 5분 후 만료되는 1회 연결 코드입니다. 기기 인증정보는 화면에 표시하지 않습니다.</div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>모바일에서 PC 주소와 인증서 지문을 함께 확인하세요.</div>
        </div>
      )}
      {recovery && qrAllowed && (
        <div role="status" style={{ marginTop: 10, padding: 10, border: "1px solid var(--color-border-default)", borderRadius: 6 }}>
          <div>기존 연결 복구 코드: <strong style={{ fontSize: "1.25rem", letterSpacing: 3 }}>{recovery.code}</strong></div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>만료: {formattedTime(recovery.expiresAt)}</div>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>선택한 기기에서 한 번만 사용할 수 있습니다. 기존 기기와 미전송 자료를 유지합니다.</div>
        </div>
      )}
      <div style={{ marginTop: 12, fontSize: "0.875rem" }}><b>등록된 모바일</b></div>
      {devices.filter((device) => !device.revoked_at).length === 0 && <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>등록된 모바일이 없습니다.</p>}
      {devices.filter((device) => !device.revoked_at).map((device) => (
        <div key={device.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "5px 0", fontSize: "0.75rem" }}>
          <span>{device.name} · 등록 {formattedTime(device.created_at)}</span>
          <button disabled={busy} onClick={() => void revoke(device)}>해제</button>
        </div>
      ))}
      <div style={{ marginTop: 12, fontSize: "0.875rem" }}><b>연결 해제된 모바일</b></div>
      {devices.filter((device) => device.revoked_at).length === 0 && <p style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>연결 해제된 모바일이 없습니다.</p>}
      {devices.filter((device) => device.revoked_at).map((device) => (
        <div key={device.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "5px 0", fontSize: "0.75rem" }}>
          <span>{device.name} · 해제 {formattedTime(device.revoked_at)}</span>
          <button disabled={busy || !qrAllowed} onClick={() => void createRecoveryCode(device)}>이 기기 다시 연결</button>
        </div>
      ))}
      {(error || status.lastError) && <p role="alert" style={{ color: "#b00", fontSize: "0.75rem" }}>오류: {error ?? status.lastError}</p>}
      {firewallError && <p role="alert" style={{ color: "#b00", fontSize: "0.75rem" }}>방화벽 상태 확인 오류: {firewallError}</p>}
      {notice && <p role="status" style={{ color: "#176b2c", fontSize: "0.75rem" }}>{notice}</p>}
    </div>
  );
}
