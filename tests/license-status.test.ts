import { test } from "node:test";
import assert from "node:assert/strict";
import {
  effectiveStatus,
  fmtKst,
  isOperator,
  kindLabel,
  licenseBanner,
  licenseNotice,
  LOGIN_REQUIRED_MESSAGE,
  OFFLINE_NOTICE_MESSAGE,
  remainingLabel,
  roleLabel,
  SERVER_UNREACHABLE_MESSAGE,
  subscriptionLabel,
  trialNotice,
  type LicenseState,
} from "../desktop/src/licenseStatus.ts";

const legacy: LicenseState = { plan: "ACTIVE", device_limit: 2, expiry: "2026-10-30", status: "active", days_left: 3 };
const trial: LicenseState = {
  plan: "TRIAL", device_limit: 0, expiry: "2026-11-10T03:00:00Z", status: "active", days_left: 7, effective_status: "active",
  kind: "trial", starts_at: "2026-11-03T03:00:00Z", ends_at: "2026-11-10T03:00:00Z", remaining_seconds: 6 * 86400 + 5 * 3600,
};

test("trial shows remaining time and exact KST end, not as a warning", () => {
  assert.equal(licenseBanner(trial), null);
  assert.equal(trialNotice(trial), "무료체험 중 · 남은 기간 6일 5시간 (종료 2026-11-10 12:00)");
  assert.equal(licenseNotice(trial, false), trialNotice(trial));
  assert.equal(subscriptionLabel(trial), "무료체험 · 사용 중 ✅");
  assert.equal(fmtKst("2026-11-10T15:00:00Z"), "2026-11-11 00:00");
  assert.equal(remainingLabel(59), "1분");
  assert.equal(remainingLabel(3 * 3600 + 120), "3시간 2분");
  assert.equal(remainingLabel(0), "0분");
});

test("expired trial vs term vs legacy subscription wording keeps data-preserving message", () => {
  const exp = { ...trial, status: "expired", effective_status: "expired", remaining_seconds: 0 };
  assert.match(licenseBanner(exp) ?? "", /무료체험 기간이 끝났습니다 \(종료 2026-11-10 12:00\).*고객 자료는 그대로 보존/);
  assert.match(licenseBanner({ ...exp, kind: "term" }) ?? "", /^이용 기간이 끝났습니다/);
  assert.match(licenseBanner({ ...legacy, status: "expired" }) ?? "", /구독 기간이 끝났습니다 \(종료 2026-10-30\)/);
  assert.equal(subscriptionLabel(exp), "무료체험 만료 ❌");
});

test("lifetime is an explicit no-end kind and not a role", () => {
  const life: LicenseState = { ...trial, kind: "lifetime", ends_at: null, expiry: null, remaining_seconds: null, days_left: null };
  assert.equal(licenseBanner(life), null);
  assert.equal(trialNotice(life), null);
  assert.equal(subscriptionLabel(life), "무기한 무료 · 사용 중 ✅");
  assert.equal(kindLabel("lifetime"), "무기한 무료");
  assert.equal(isOperator(life), false);
  assert.equal(roleLabel(life.role), "일반");
});

test("suspended (and legacy revoked) are distinct from expiry", () => {
  const s = { ...trial, status: "suspended", effective_status: "suspended" };
  assert.match(licenseBanner(s) ?? "", /이용이 정지되었습니다/);
  assert.equal(subscriptionLabel(s), "이용정지 ❌");
  assert.match(licenseBanner({ ...legacy, status: "revoked" }) ?? "", /이용이 정지/);
});

test("owner role from server suppresses banners without hiding raw status", () => {
  const s: LicenseState = { ...trial, status: "expired", role: "owner", effective_status: "active" };
  assert.equal(licenseBanner(s), null);
  assert.equal(trialNotice(s), null);
  assert.equal(effectiveStatus(s), "active");
  assert.match(subscriptionLabel(s), /소유자 계정/);
  assert.ok(isOperator(s));
});

test("client never self-elevates and none explains one trial per account", () => {
  const s: LicenseState = { ...legacy, status: "none" };
  assert.equal(isOperator(s), false);
  assert.match(licenseBanner(s) ?? "", /계정당 1회/);
});

test("offline notice is informational and combines with trial/expiry banners", () => {
  assert.equal(licenseNotice(legacy, false), null);
  assert.equal(licenseNotice(legacy, true), OFFLINE_NOTICE_MESSAGE);
  assert.match(licenseNotice(trial, true) ?? "", /무료체험 중.*오프라인 최대 5일/);
  assert.match(licenseNotice({ ...trial, status: "expired", effective_status: "expired" }, true) ?? "", /무료체험 기간이 끝났습니다/);
  for (const m of [LOGIN_REQUIRED_MESSAGE, SERVER_UNREACHABLE_MESSAGE]) {
    assert.ok(m.includes("고객 자료는 그대로"));
  }
  assert.ok(SERVER_UNREACHABLE_MESSAGE.includes("이용 기간 만료가 아니며"));
});
