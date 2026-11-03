// 라이선스/인증 상태를 사용자 문구로 구분한다.
// 이용권 상태(체험 중·만료·정지·없음)와 접속 상태(로그인 필요·인증 서버 연결 실패)를 섞지 않는다.
// 7일 무료체험과 5일 오프라인 유예는 다른 개념이다: 유예는 "서버 확인 없이 쓸 수 있는 기간",
// 체험/기간 종료는 서명된 종료 시각으로 판정한다(유예가 종료 시각을 늘리지 않는다).
// 어떤 경우에도 고객 자료를 지우지 않는다 — 문구만 결정한다. 기능 제한(강제 통제)은 아직 정책 미정이라 넣지 않았다.

export type LicenseState = {
  plan: string;
  device_limit: number;
  expiry: string | null; // 종료(ISO 시각 또는 기존 로컬 라이선스의 날짜). lifetime은 null
  status: string; // 이용권 자체: active | expired | suspended(revoked) | none | scheduled
  days_left: number | null;
  starts_at?: string | null;
  revoked_at?: string | null;
  role?: "owner" | "admin" | null;
  effective_status?: string; // 서버가 역할까지 반영한 사용 가능 상태
  kind?: "trial" | "term" | "lifetime" | null; // 중앙 이용권 종류
  ends_at?: string | null;
  suspended_at?: string | null;
  trial_started_at?: string | null;
  remaining_seconds?: number | null;
};

export type LicenseResolution =
  | { kind: "ok"; state: LicenseState; offline: boolean }
  | { kind: "login_required"; message: string }
  | { kind: "server_unreachable"; message: string };

/** 서버 판단(effective_status)을 우선, 구버전 서버 응답이면 status를 쓴다. */
export function effectiveStatus(state: LicenseState): string {
  return state.effective_status ?? state.status;
}

/** 한국 표준시 "YYYY-MM-DD HH:mm" (ISO 시각). 날짜만 있는 값은 그대로. */
export function fmtKst(iso: string | null | undefined): string {
  if (!iso) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t + 9 * 3600 * 1000).toISOString().slice(0, 16).replace("T", " ");
}

/** 남은 기간 문구(초 단위). */
export function remainingLabel(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  if (seconds <= 0) return "0분";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return h > 0 ? `${d}일 ${h}시간` : `${d}일`;
  if (h > 0) return `${h}시간 ${m}분`;
  return `${Math.max(1, m)}분`;
}

export function kindLabel(kind: LicenseState["kind"]): string {
  return kind === "trial" ? "무료체험" : kind === "term" ? "기간 이용" : kind === "lifetime" ? "무기한 무료" : "";
}

export function licenseBanner(state: LicenseState): string | null {
  const s = effectiveStatus(state);
  const end = state.ends_at ?? state.expiry;
  if (s === "expired") {
    const until = end ? ` (종료 ${fmtKst(end)})` : "";
    const what = state.kind === "trial" ? "무료체험 기간이" : state.kind ? "이용 기간이" : "구독 기간이";
    return `${what} 끝났습니다${until}. 고객 자료는 그대로 보존됩니다. 계속 사용하려면 관리자에게 문의하세요.`;
  }
  if (s === "suspended" || s === "revoked") {
    return "이용이 정지되었습니다. 고객 자료는 그대로 보존됩니다. 관리자에게 문의하세요.";
  }
  if (s === "none") {
    return "이용권이 없습니다(무료체험은 이메일 확인 후 계정당 1회). 고객 자료는 그대로 보존됩니다. 관리자에게 문의하세요.";
  }
  if (s === "scheduled") {
    const from = state.starts_at ? ` (시작 ${fmtKst(state.starts_at)})` : "";
    return `이용권 시작 전입니다${from}. 고객 자료는 그대로 보존됩니다.`;
  }
  return null;
}

export const OFFLINE_NOTICE_MESSAGE =
  "인증 서버에 연결하지 못해 마지막으로 확인된 이용권으로 사용 중입니다(오프라인 최대 5일). 고객 자료는 그대로 있습니다.";

/** 진행 중인 무료체험 안내(남은 기간). 소유자/무기한/기간이용은 표시하지 않는다. */
export function trialNotice(state: LicenseState): string | null {
  if (state.role === "owner" || state.kind !== "trial" || effectiveStatus(state) !== "active") return null;
  return `무료체험 중 · 남은 기간 ${remainingLabel(state.remaining_seconds)} (종료 ${fmtKst(state.ends_at)})`;
}

/** 메인 화면 배너 문구: 이용권 상태 우선, 체험 중 남은 기간, 오프라인 판정이면 안내만 한다(차단 없음). */
export function licenseNotice(state: LicenseState, offline: boolean): string | null {
  const parts = [licenseBanner(state) ?? trialNotice(state), offline ? OFFLINE_NOTICE_MESSAGE : null].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

export function roleLabel(role: LicenseState["role"]): string {
  return role === "owner" ? "소유자" : role === "admin" ? "관리자" : "일반";
}

export function subscriptionLabel(state: LicenseState): string {
  const s = effectiveStatus(state);
  if (state.role === "owner") return "소유자 계정 — 이용권과 무관하게 사용 ✅";
  if (s === "expired") return `${kindLabel(state.kind) || "이용권"} 만료 ❌`;
  if (s === "suspended" || s === "revoked") return "이용정지 ❌";
  if (s === "none") return "이용권 없음";
  if (s === "scheduled") return "시작 전";
  return state.kind ? `${kindLabel(state.kind)} · 사용 중 ✅` : "활성 ✅";
}

/** 이용권 기록 자체의 상태(역할 반영 전). 소유자 화면에서 '권한'과 분리해 보여줄 때 쓴다. */
function recordStatusLabel(state: LicenseState): string {
  const s = state.status;
  const end = state.ends_at ?? state.expiry;
  if (s === "suspended" || s === "revoked") return "정지됨";
  if (s === "none") return "없음";
  if (state.kind === "lifetime") return "무기한";
  if (s === "expired" || (end && Date.parse(end) <= Date.now())) return end ? `기간 종료(${fmtKst(end)})` : "기간 종료";
  if (s === "scheduled") return state.starts_at ? `시작 전(${fmtKst(state.starts_at)})` : "시작 전";
  return end ? `진행 중(종료 ${fmtKst(end)})` : "진행 중";
}

/**
 * 설정 화면 '라이선스' 표 행. 소유자는 권한(사용 가능)과 일반 이용권 기록을 분리해서,
 * 지난 무료체험 종료일이 사용 종료처럼 보이지 않게 한다. 권한 판정 자체는 바꾸지 않는다(표시만).
 */
export function licenseRows(state: LicenseState & { offline?: boolean }): [string, string][] {
  const off = state.offline ? " (오프라인 확인)" : "";
  if (state.role === "owner") {
    const rows: [string, string][] = [
      ["역할", "소유자"],
      ["사용 상태", `사용 가능 ✅ — 소유자 권한(이용권 기간과 무관)${off}`],
    ];
    if (state.kind || state.status !== "none") {
      rows.push(["일반 이용권 기록", `${kindLabel(state.kind) || "이용권"} · ${recordStatusLabel(state)} — 소유자 사용에는 영향 없음`]);
    }
    return rows;
  }
  const rows: [string, string][] = [
    ["플랜", `${state.plan}${off}`],
    ["상태", subscriptionLabel(state)],
  ];
  if (state.role) rows.push(["역할", roleLabel(state.role)]);
  if (state.kind) rows.push(["이용 형태", kindLabel(state.kind)]);
  if (state.starts_at) {
    const confirmed = state.kind === "trial" && state.trial_started_at === state.starts_at ? " (이메일 확인 완료)" : "";
    rows.push(["시작", `${fmtKst(state.starts_at)}${confirmed}`]);
  }
  const end = state.ends_at ?? state.expiry;
  rows.push(["종료", state.kind === "lifetime" || !end ? "없음(무기한)" : fmtKst(end)]);
  if (state.remaining_seconds != null) rows.push(["남은 기간", remainingLabel(state.remaining_seconds)]);
  return rows;
}

export function isOperator(state: LicenseState | null | undefined): boolean {
  return state?.role === "owner" || state?.role === "admin";
}

export const LOGIN_REQUIRED_MESSAGE = "로그인 세션이 만료되었습니다. 다시 로그인하세요. 고객 자료는 그대로 있습니다.";
/** 중앙 회원 빌드에서 이 PC 기존 계정만으로 들어오려 할 때(로컬 이용권 우회 차단). 자료는 지우지 않는다. */
export const CENTRAL_ACCOUNT_REQUIRED_MESSAGE =
  "이 PC 기존 계정만으로는 사용할 수 없습니다. 모아둠 계정으로 로그인하세요. 이 PC 계정은 로그인 후 설정 > '이 PC 기존 계정 연결'에서 연결할 수 있고, 고객 자료는 이 PC에 그대로 있습니다.";

/**
 * 이 PC 기존 계정(로컬 control-server 이용권) 세션을 앱 사용 근거로 인정할지.
 * 중앙 회원 빌드에서는 인정하지 않는다 — 로컬 이용권은 고정 개발용 비밀로 서명되어 중앙 이용권을 우회할 수 있다.
 * 로컬 전용 빌드(중앙 설정 없음)는 기존 동작 그대로.
 */
export function localSessionAllowed(centralBuild: boolean): boolean {
  return !centralBuild;
}
export const SERVER_UNREACHABLE_MESSAGE =
  "인증 서버에 연결할 수 없고, 오프라인 사용 가능 기간도 지났습니다. 앱을 다시 시작하거나 다시 로그인하세요. 이용 기간 만료가 아니며 고객 자료는 그대로 있습니다.";
