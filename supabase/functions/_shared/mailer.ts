// 안내 메일 발송 루프(순수 로직 — DB/공급자는 주입). Edge Function과 Node 테스트가 같은 코드를 쓴다.
// 규칙: 공급자가 메시지 ID를 돌려준 경우에만 sent. 공급자 미설정이면 아무것도 가져오지 않는다(발송 완료 주장 금지).
import { type MailKind, renderMail } from "./mail_templates.ts";

export type OutboxRow = { id: number; to_email: string; kind: MailKind; payload: Record<string, unknown>; attempts: number };

export type MailerDeps = {
  providerConfigured: boolean;
  enqueueExpiryNotices(): Promise<number>;
  claim(batch: number): Promise<OutboxRow[]>;
  send(msg: { to: string; subject: string; text: string; html: string; idempotencyKey: string }): Promise<{ id: string }>;
  result(id: number, ok: boolean, error: string | null, providerId: string | null): Promise<string>;
  accountUrl: string;
};

export type MailerReport = { configured: boolean; enqueued: number; claimed: number; sent: number; retry: number; failed: number };

export async function runMailer(deps: MailerDeps, batch = 20): Promise<MailerReport> {
  const report: MailerReport = { configured: deps.providerConfigured, enqueued: 0, claimed: 0, sent: 0, retry: 0, failed: 0 };
  report.enqueued = await deps.enqueueExpiryNotices();
  if (!deps.providerConfigured) return report; // 발송함은 쌓이되 보내지 않는다
  const rows = await deps.claim(batch);
  report.claimed = rows.length;
  for (const row of rows) {
    let outcome: string;
    try {
      const mail = renderMail(row.kind, row.payload as Record<string, any>, deps.accountUrl);
      const res = await deps.send({ to: row.to_email, ...mail, idempotencyKey: `moadoom-mail-${row.id}` });
      outcome = await deps.result(row.id, true, null, res?.id ? String(res.id) : null);
    } catch (e) {
      outcome = await deps.result(row.id, false, e instanceof Error ? e.message.slice(0, 300) : String(e).slice(0, 300), null);
    }
    if (outcome === "sent") report.sent++;
    else if (outcome === "failed") report.failed++;
    else report.retry++;
  }
  return report;
}
