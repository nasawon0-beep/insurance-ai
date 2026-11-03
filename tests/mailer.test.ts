// 안내 메일 템플릿/발송 루프(공급자·DB는 대역). 실제 메일 발송 검증이 아니다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMail } from "../supabase/functions/_shared/mail_templates.ts";
import { type MailerDeps, type OutboxRow, runMailer } from "../supabase/functions/_shared/mailer.ts";

const URL = "https://example.test/account/";

test("templates are informational Korean mails with KST times and no ad copy", () => {
  const w = renderMail("welcome_trial", { starts_at: "2026-11-03T03:00:00Z", ends_at: "2026-11-10T03:00:00Z", days: 7 }, URL);
  assert.equal(w.subject, "[모아둠] 무료체험 7일이 시작되었습니다");
  assert.match(w.text, /시작: 2026-11-03 12:00 \/ 종료: 2026-11-10 12:00/);
  assert.match(w.text, /계정당 1회.*자동 결제는 없습니다/);
  const life = renderMail("entitlement_changed", { action: "lifetime", state: { kind: "lifetime", ends_at: null, status: "active" } }, URL);
  assert.match(life.text, /무기한 무료 이용이 지정되었습니다[\s\S]*종료일 없이/);
  const exp = renderMail("expiry_notice", { kind: "trial", ends_at: "2026-11-10T03:00:00Z", days_before: 3 }, URL);
  assert.equal(exp.subject, "[모아둠] 무료체험 종료 3일 전 안내");
  for (const m of [w, life, exp]) {
    assert.ok(m.html.includes(URL) && !/할인|이벤트|광고|프로모션/.test(m.text));
  }
  const xss = renderMail("entitlement_changed", { action: "<script>", state: {} }, URL);
  assert.ok(!xss.html.includes("<script>"));
});

function fakeDb(rows: OutboxRow[]) {
  const results: Array<[number, boolean, string | null, string | null]> = [];
  return {
    results,
    deps(over: Partial<MailerDeps> = {}): MailerDeps {
      return {
        providerConfigured: true,
        accountUrl: URL,
        enqueueExpiryNotices: async () => 1,
        claim: async () => rows.splice(0),
        send: async (m) => ({ id: `prov-${m.idempotencyKey}` }),
        result: async (id, ok, err, pid) => { results.push([id, ok, err, pid]); return ok && pid ? "sent" : "queued"; },
        ...over,
      };
    },
  };
}

const row = (id: number): OutboxRow => ({ id, to_email: "a@example.test", kind: "welcome_trial", attempts: 1,
  payload: { starts_at: "2026-11-03T03:00:00Z", ends_at: "2026-11-10T03:00:00Z", days: 7 } });

test("mailer sends only when a provider is configured and records provider ids", async () => {
  const db = fakeDb([row(1), row(2)]);
  const off = await runMailer(db.deps({ providerConfigured: false }));
  assert.deepEqual(off, { configured: false, enqueued: 1, claimed: 0, sent: 0, retry: 0, failed: 0 });
  assert.equal(db.results.length, 0); // 공급자 없으면 아무 것도 "보냄" 처리하지 않음
  const on = await runMailer(db.deps());
  assert.equal(on.sent, 2);
  assert.deepEqual(db.results.map((r) => r[3]), ["prov-moadoom-mail-1", "prov-moadoom-mail-2"]);
});

test("provider failure is recorded for retry, not as sent", async () => {
  const db = fakeDb([row(7)]);
  const rep = await runMailer(db.deps({ send: async () => { throw new Error("provider 503"); } }));
  assert.deepEqual([rep.sent, rep.retry], [0, 1]);
  assert.deepEqual(db.results[0], [7, false, "provider 503", null]);
});
