import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./MobileConnectionPanel.css";
import { mobileDeviceDisplayName, mobileStatusPollMs, pairingQrCloseReason } from "./mobileSyncPolicy.ts";
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
  const [devicesKnown, setDevicesKnown] = useState(false);
  const [statusKnown, setStatusKnown] = useState(false);
  const [slow, setSlow] = useState(false);
  const [networkHelp, setNetworkHelp] = useState<"choose" | "hotspot" | "home" | "later">("choose");
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
  useEffect(() => { setNetworkHelp("choose"); }, [firewallStatus?.networkCategory]);
  const qrAllowed = visible && statusKnown && firewallQrAllowed(status.running, firewallStatus) && !firewallBusy;
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

  // UI deadline only: never release native locks or assume a timed-out action succeeded.
  useEffect(() => {
    setSlow(false);
    if (!visible || (statusKnown && !status.starting && !busy)) return;
    const timer = window.setTimeout(() => setSlow(true), 20000);
    return () => window.clearTimeout(timer);
  }, [visible, statusKnown, status.starting, busy]);

  const refresh = useCallback(async () => {
    const next = await invoke<MobileSyncStatus>("mobile_sync_status");
    setStatus(next);
    setStatusKnown(true);
    try {
      setDevices(await invoke<MobileDevice[]>("mobile_sync_list_devices"));
      setDevicesKnown(true);
    } catch {
      setDevicesKnown(false);
    }
    const preference = await invoke<MobileStartupPreference>("mobile_sync_startup_preference");
    setRestoreOnAppStart(preference.restoreOnAppStart);
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
      void invoke<MobileSyncStatus>("mobile_sync_status").then((next) => { setStatus(next); setStatusKnown(true); }).catch(() => setStatusKnown(false));
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs]);

  // QR 표시 중: 1초마다 만료 확인, 4초마다 새 기기 등록 여부 확인.
  useEffect(() => {
    if (!qr) return;
    const tick = window.setInterval(() => setNowSeconds(Math.floor(Date.now() / 1000)), 1000);
    const devicesTimer = window.setInterval(() => {
      void invoke<MobileDevice[]>("mobile_sync_list_devices").then((next) => { setDevices(next); setDevicesKnown(true); }).catch(() => setDevicesKnown(false));
      void invoke<MobileSyncStatus>("mobile_sync_status").then((next) => { setStatus(next); setStatusKnown(true); }).catch(() => setStatusKnown(false));
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
    setNotice("연결을 시작했습니다. 필요한 경우 ‘휴대폰 연결 허용’을 승인한 뒤 QR을 보여 주세요.");
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
    if (!window.confirm(`‘${mobileDeviceDisplayName(device.name)}’ 기기를 기존 등록 정보와 미전송 자료를 유지한 채 다시 연결할까요?`)) return;
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
    if (!window.confirm(`‘${mobileDeviceDisplayName(device.name)}’ 기기의 모바일 연결을 해제할까요?`)) return;
    await invoke("mobile_sync_revoke_device", { deviceId: device.id });
    setNotice("기기 연결을 해제했습니다. 기존 인증 정보로는 더 이상 동기화할 수 없습니다.");
  });
  const firewall = (enable: boolean) => run(async () => {
    if (!firewallStatus?.supported || firewallStatus.qa || (enable && (!status.running || !firewallStatus.canApply))) {
      throw new Error("현재 환경에서는 방화벽 변경을 승인할 수 없습니다. QA에서는 실제 규칙을 변경하지 않습니다.");
    }
    const message = enable
      ? "휴대폰 연결을 허용할까요? 다음 Windows 권한 요청에서 승인해 주세요. 모아둠에만 같은 개인 네트워크의 휴대폰 연결을 허용합니다. 공용·회사 네트워크 설정이나 방화벽 전체는 바꾸지 않습니다."
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
      setNotice(enable ? "휴대폰 연결 허용을 확인했습니다. 새 휴대폰은 QR로 등록해 주세요." : "모바일 연결 방화벽 규칙을 비활성화했습니다(삭제 아님).");
    } finally {
      setFirewallBusy(false);
    }
  });

  const needsPermission = statusKnown && status.running && firewallStatus?.supported
    && firewallStatus.networkCategory === "private" && firewallStatus.canApply
    && !firewallStatus.qa && !firewallStatus.lastError && !firewallStatus.ruleReady
    && ["missing", "disabled", "outdated", "ineffective"].includes(firewallStatus.ruleState);
  const nextStep = slow
    ? "응답이 늦습니다. Windows 승인 창을 확인하고 아래 상세 정보에서 상태를 다시 확인하세요."
    : busy ? "요청을 처리하고 있습니다. Windows 승인 창이 열렸다면 승인 또는 취소해 주세요."
    : !statusKnown ? "연결 상태를 확인할 수 없으면 아래 상세 정보에서 ‘상태 다시 확인’을 눌러 주세요."
    : status.starting ? "연결을 준비하고 있습니다. 잠시 후 QR로 폰 연결을 눌러 주세요."
    : !status.running ? "먼저 ‘연결 시작’을 눌러 주세요."
    : firewallStatus?.networkCategory === "public" || firewallStatus?.networkCategory === "domain"
      ? "신뢰할 수 있는 개인 Wi-Fi 또는 핫스팟인지 확인하고 아래 상세 정보를 열어 주세요."
    : needsPermission ? "‘휴대폰 연결 허용’을 누른 뒤 Windows 권한 요청을 승인해 주세요."
    : !qrAllowed ? "새 기기 등록을 위한 허용 상태를 확인하지 못했습니다. 아래 상세 정보에서 ‘방화벽 상태 새로고침’을 눌러 주세요."
    : "새 휴대폰은 ‘QR로 폰 연결’을 누르고, 등록된 휴대폰은 모아둠 앱에서 동기화해 주세요.";

  const row = (label: string, value: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: "0.9375rem", padding: "5px 0" }}>
      <span style={{ flex: "0 0 100px", color: "var(--color-text-primary)" }}>{label}</span>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );

  return (
    <div className="mobile-connection" style={{ border: "1px solid var(--color-border-default)", borderRadius: 8, padding: 12, marginBottom: 12, background: "var(--color-bg-surface)" }}>
      <div style={{ marginBottom: 8 }}><b>모바일 연결</b></div>
      <p style={{ fontSize: "0.9375rem", lineHeight: 1.7 }}>같은 Wi-Fi 또는 핫스팟 연결 → 연결 시작 → QR로 폰 연결</p>
      <p role="status" style={{ fontSize: "0.9375rem" }}><b className={devicesKnown && activeDevices > 0 ? "mc-registered" : ""}>{devicesKnown ? `등록된 휴대폰 ${activeDevices}대` : "등록 기기 목록 확인 필요"}</b>
        <span style={{ color: "var(--color-text-primary)" }}> · {statusKnown ? status.running ? "PC 연결 기능 켜짐" : status.starting ? slow ? "연결 시작 지연" : "연결 준비 중" : "PC 연결 기능 꺼짐" : "PC 상태 확인 필요"}</span>
      </p>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 8 }}>
        {!status.running && <div className="mc-action"><button className="mc-primary" disabled={busy || !statusKnown || status.starting} onClick={() => void start()}>연결 시작</button><span>PC에서 연결 준비</span></div>}
        {status.running && <div className="mc-action"><button disabled={busy} onClick={() => void stop()}>연결 중지</button><span>PC 연결 기능 끄기</span></div>}
        <div className="mc-action"><button className="mc-primary" disabled={busy || !qrAllowed} onClick={() => void createQr()}>QR로 폰 연결</button><span>휴대폰 카메라로 등록</span></div>
        <div className="mc-action"><button title="QR 대신 직접 입력할 1회용 코드 만들기" disabled={busy || !qrAllowed} onClick={() => void createCode()}>새 기기 등록 코드</button><span>카메라 없이 직접 입력</span></div>
      </div>
      {firewallStatus?.networkCategory !== "public" && (!qrAllowed || busy) && <p className="mc-attention" role="status">{nextStep}</p>}
      {needsPermission && <button className="mc-primary" disabled={busy || firewallBusy} onClick={() => void firewall(true)}>휴대폰 연결 허용</button>}
      {firewallStatus?.networkCategory === "public" && (
        <section aria-label="네트워크 확인" className="mc-network">
          {networkHelp === "later" ? <p className="mc-attention">네트워크 확인 보류 <button onClick={() => setNetworkHelp("choose")}>다시 안내</button></p> : <>
            <p className="mc-attention">휴대폰 연결을 위한 네트워크 확인이 필요합니다. 카페·공공 Wi-Fi라면 휴대폰의 핫스팟을 켜고 PC를 연결해 주세요.</p>
            <div className="mc-choices">
              <button className="mc-primary" onClick={() => setNetworkHelp("hotspot")}>핫스팟 연결 방법</button>
              <button onClick={() => setNetworkHelp("home")}>집·개인 Wi-Fi를 사용 중이에요</button>
              <button onClick={() => setNetworkHelp("later")}>나중에 연결</button>
            </div>
            {networkHelp === "hotspot" && <p>본인 휴대폰의 핫스팟 켜기 → PC의 Wi-Fi를 그 핫스팟에 연결 → 아래에서 다시 확인해 주세요.</p>}
            {networkHelp === "home" && <p>본인이나 가족이 관리하는 신뢰할 수 있는 Wi-Fi인가요? 그렇다면 Windows 설정 → 네트워크 및 인터넷 → Wi-Fi → 연결된 네트워크의 속성에서 ‘개인 네트워크’를 선택한 뒤 다시 확인해 주세요.</p>}
            {networkHelp !== "choose" && <p>설정은 자동으로 바꾸지 않습니다. 네트워크에 따라 연결이 제한될 수 있습니다.</p>}
          </>}
          <button disabled={firewallBusy} onClick={() => { invalidatePairing(); setFirewallStatus(null); firewallPoller.current?.refresh(true); }}>네트워크 다시 확인</button>
        </section>
      )}
      <details style={{ fontSize: "0.9375rem", marginTop: 12, marginBottom: 8 }}>
        <summary>연결 문제 해결 · 상세 정보</summary>
        <p>등록된 기기 수는 현재 접속이나 동기화 완료를 뜻하지 않습니다. 최근 동기화 결과는 휴대폰 앱에서 확인하세요.</p>
        <p>QR 대신 직접 입력하려면 ‘새 기기 등록 코드’를 사용하세요.</p>
        {row("서버 상태", statusKnown ? status.running ? "실행 중" : status.starting ? slow ? "시작 시간 초과 — 상태 다시 확인 필요" : "시작 중" : "중지됨" : "조회 필요")}
        {row("PC 주소", status.url ?? "연결을 시작하면 표시됩니다")}
        {row("인증서 지문", status.certificateFingerprint ?? "-")}
        {row("인증서 만료", formattedTime(status.certificateExpiresAt))}
        {row("네트워크 종류", firewallStatus?.networkCategory ?? "조회되지 않음")}
        {row("방화벽 설정", firewallBusy ? slow ? "승인 응답 지연 — Windows 창 확인" : "Windows 승인 요청 중" : firewallQrAllowed(true, firewallStatus) ? "Private / LocalSubnet 규칙 확인됨" : "새 기기 등록 허용 여부 확인 필요")}
        {row("규칙 상태", firewallStatus?.ruleState ?? "unknown")}
        {row("프로그램", firewallStatus?.programPath ?? "-")}
        {row("규칙 이름", firewallStatus?.ruleName ?? "-")}
        <p>연결 시간 초과 시 같은 Wi-Fi/핫스팟인지 확인하세요. 게스트·회사망, VPN은 기기 간 통신을 막을 수 있습니다. 방화벽 전체를 끄지 마세요.</p>
        <p>허용 범위는 설치된 local-engine.exe의 TCP 18443, Private / LocalSubnet입니다. Public·Domain 네트워크를 자동으로 변경하지 않습니다. 팀 공유·인터넷 원격 동기화 기능이 아닙니다.</p>
        {firewallStatus?.qa && <p>QA 모드: 실제 방화벽 변경은 금지됩니다.</p>}
        <label style={{ display: "flex", gap: 8, alignItems: "center", minHeight: 44 }}>
          <input type="checkbox" checked={restoreOnAppStart} disabled={busy} onChange={(event) => void setStartupRestore(event.currentTarget.checked)} />
          로그인 후 모바일 연결 자동 복원
        </label>
        <p>Windows 부팅 서비스는 만들지 않습니다. ‘연결 중지’는 자동 복원도 끕니다.</p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button disabled={busy} onClick={() => void refresh().catch((reason) => setError(String(reason)))}>상태 다시 확인</button>
          <button disabled={firewallBusy} onClick={() => firewallPoller.current?.refresh()}>방화벽 상태 새로고침</button>
          <button disabled={busy || !firewallStatus?.supported || firewallStatus?.qa} onClick={() => void firewall(false)}>방화벽 규칙 비활성화</button>
        </div>
        {(error || status.lastError) && <p style={{ color: "var(--mc-error)", overflowWrap: "anywhere" }}>상세 오류: {error ?? status.lastError}</p>}
        {firewallError && <p style={{ color: "var(--mc-error)", overflowWrap: "anywhere" }}>방화벽 상태 확인 오류: {firewallError}</p>}
      </details>
      {qr && qrAllowed && (
        <div role="status" style={{ marginTop: 10, padding: 10, border: "1px solid var(--color-border-default)", borderRadius: 6, background: "var(--color-bg-surface)" }}>
          <div style={{ fontSize: "0.9375rem", marginBottom: 6 }}><b>폰의 모아둠 앱 → PC 동기화 → ‘PC QR 스캔’으로 찍어 주세요</b></div>
          <img
            alt="모바일 연결 QR"
            src={`data:image/svg+xml;base64,${btoa(qr.svg)}`}
            style={{ width: 240, maxWidth: "100%", height: "auto", display: "block", imageRendering: "pixelated" }}
          />
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>
            남은 시간 {Math.max(0, qr.expiresAt - nowSeconds)}초 · 1회용 · 이 PC 주소·인증서 지문·등록 코드가 담겨 있습니다.
          </div>
          <details style={{ fontSize: "0.9375rem", marginTop: 6 }}>
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
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>만료: {formattedTime(pairing.expiresAt)}</div>
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>이 값은 5분 후 만료되는 1회 연결 코드입니다. 기기 인증정보는 화면에 표시하지 않습니다.</div>
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>모바일에서 PC 주소와 인증서 지문을 함께 확인하세요.</div>
        </div>
      )}
      {recovery && qrAllowed && (
        <div role="status" style={{ marginTop: 10, padding: 10, border: "1px solid var(--color-border-default)", borderRadius: 6 }}>
          <div>기존 연결 복구 코드: <strong style={{ fontSize: "1.25rem", letterSpacing: 3 }}>{recovery.code}</strong></div>
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>만료: {formattedTime(recovery.expiresAt)}</div>
          <div style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>선택한 기기에서 한 번만 사용할 수 있습니다. 기존 기기와 미전송 자료를 유지합니다.</div>
        </div>
      )}
      <div style={{ marginTop: 12, fontSize: "0.9375rem" }}><b>등록된 휴대폰</b></div>
      {!devicesKnown && <p style={{ fontSize: "0.9375rem" }}>기기 목록을 확인하지 못했습니다. 상세 정보에서 ‘상태 다시 확인’을 눌러 주세요.</p>}
      {devicesKnown && activeDevices === 0 && <p style={{ fontSize: "0.9375rem", color: "var(--color-text-primary)" }}>아직 등록된 휴대폰이 없습니다.</p>}
      {devices.filter((device) => !device.revoked_at).map((device) => (
        <div key={device.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "5px 0", fontSize: "0.9375rem" }}>
          <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{mobileDeviceDisplayName(device.name)} · 등록 {formattedTime(device.created_at)}</span>
          <button disabled={busy} onClick={() => void revoke(device)}>해제</button>
        </div>
      ))}
      <details style={{ marginTop: 12, fontSize: "0.9375rem" }}>
      <summary>해제된 휴대폰 ({devices.filter((device) => device.revoked_at).length})</summary>
      <p>다시 연결하면 기존 등록 정보와 미전송 자료를 유지합니다.</p>
      {devices.filter((device) => device.revoked_at).map((device) => (
        <div key={device.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "5px 0", fontSize: "0.9375rem" }}>
          <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{mobileDeviceDisplayName(device.name)} · 해제 {formattedTime(device.revoked_at)}</span>
          <button disabled={busy || !qrAllowed} onClick={() => void createRecoveryCode(device)}>이 기기 다시 연결</button>
        </div>
      ))}
      </details>
      {(error || status.lastError) && <p role="alert" style={{ color: "var(--mc-error)", fontSize: "0.9375rem" }}>요청을 완료하지 못했습니다. 연결 문제 해결 · 상세 정보를 확인해 주세요.</p>}
      {notice && <p role="status" style={{ color: "var(--color-text-primary)", fontSize: "0.9375rem" }}>{notice}</p>}
    </div>
  );
}
