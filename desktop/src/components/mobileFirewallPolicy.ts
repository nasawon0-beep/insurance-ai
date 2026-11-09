export type MobileFirewallStatus = {
  supported: boolean;
  qa: boolean;
  networkCategory: 'private' | 'public' | 'domain' | 'unavailable';
  ruleState: string;
  ruleReady: boolean;
  canApply: boolean;
  programPath: string | null;
  ruleName: string | null;
  connectivityVerified: false;

  lastError: string | null;
  applyOutcome: null | 'applied' | 'failed' | 'blocked' | 'qaRejected' | 'notConfirmed';
};

/** Read-only single-flight polling. A timed-out native process still owns the slot until it exits. */
export function createFirewallPoller(
  query: () => Promise<MobileFirewallStatus>,
  accept: (status: MobileFirewallStatus) => void,
  reject: (message: string) => void,
  timeoutMs = 12000,
) {
  let disposed = false;
  let pending = false;
  let generation = 0;
  let queued = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const poller = {
    refresh(invalidate = false) {
      if (disposed) return;
      if (invalidate) generation++;
      if (pending) { queued ||= invalidate; return; }
      const issued = generation;
      pending = true;
      let expired = false;
      timer = setTimeout(() => {
        expired = true;
        if (!disposed) reject('방화벽 상태 조회 시간이 초과되었습니다. 휴대폰 연결 여부는 확인되지 않았습니다.');
      }, timeoutMs);
      void (async () => {
        try {
          const result = await query();
          if (!disposed && !expired && issued === generation) accept(result);
        } catch (error) {
          if (!disposed && !expired && issued === generation) reject(String(error));
        } finally {
          clearTimeout(timer);
          pending = false;
          if (queued && !disposed) { queued = false; poller.refresh(); }
        }
      })();
    },
    dispose() { disposed = true; clearTimeout(timer); },
  };
  return poller;
}

export function firewallApplyError(status: MobileFirewallStatus, enable: boolean): string | null {
  const verified = enable ? status.ruleReady : ['disabled', 'missing'].includes(status.ruleState);
  if (status.applyOutcome !== 'applied' || status.lastError || !verified) {
    return status.lastError || '방화벽 변경이 취소·거부되었거나 설정을 확인하지 못했습니다. 상태를 새로고침한 뒤 다시 시도하세요.';
  }
  return null;
}

export function firewallQrAllowed(running: boolean, status: MobileFirewallStatus | null, publicConfirmed = false): boolean {
  return running && !!status?.supported && (status.networkCategory === 'private'
    || (status.networkCategory === 'public' && publicConfirmed && !status.qa))
    && status.ruleReady && !status.lastError;
}

export function mobileConnectionFailure(message: string): string {
  if (/UAC|승인|notConfirmed|qaRejected/i.test(message)) return 'Windows 승인이 취소·거부되었거나 완료되지 않았습니다. 상태를 확인한 뒤 다시 시도해 주세요.';
  if (/TLS|SSL|certificate|인증서/i.test(message)) return '보안 연결을 확인하지 못했습니다. PC 주소와 인증서 정보를 확인해 주세요. 인증서 검증을 끄지 마세요.';
  if (/401|인증 정보|revoked/i.test(message)) return '휴대폰 인증 정보를 확인해야 합니다. 기존 자료를 유지한 채 다시 연결해 주세요.';
  if (/timeout|timed out|시간 초과|connect|network|연결.*못/i.test(message)) return '연결하지 못했습니다. 휴대폰 핫스팟이나 다른 Wi-Fi에 PC와 휴대폰을 연결한 뒤 다시 시도해 주세요.';
  return '요청을 완료하지 못했습니다. 연결 문제 해결 · 상세 정보를 확인해 주세요.';
}
