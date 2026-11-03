// Supabase Edge Function: moadoom-mailer (예약 실행 전용 — 회원/브라우저가 호출하지 않음)
// - 종료 전 안내를 발송함에 넣고(설정 일수), 대기 메일을 공급자로 보낸 뒤 결과를 기록한다.
// - 호출 인증: MOADOOM_MAILER_CRON_SECRET 헤더(x-moadoom-cron). 서비스 키는 함수 런타임 env(SUPABASE_SERVICE_ROLE_KEY)에서만 쓴다.
// - 공급자: 현재 HTTP 메일 API(Resend 호환) 1종. MOADOOM_MAIL_API_KEY/MOADOOM_MAIL_FROM 미설정이면 발송하지 않고 configured=false를 돌려준다.
//   (SMTP 계정·발신 도메인 확보는 사용자 결정 blocker. 실제 발송 확인 전 "발송 완료"라고 보고하지 않는다.)
import { createClient } from "jsr:@supabase/supabase-js@2";
import { type OutboxRow, runMailer } from "../_shared/mailer.ts";

Deno.serve(async (req) => {
  const secret = Deno.env.get("MOADOOM_MAILER_CRON_SECRET") ?? "";
  if (!secret || req.headers.get("x-moadoom-cron") !== secret) {
    return new Response(JSON.stringify({ error: "forbidden" }), { status: 403 });
  }
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const apiKey = Deno.env.get("MOADOOM_MAIL_API_KEY") ?? "";
  const from = Deno.env.get("MOADOOM_MAIL_FROM") ?? "";
  const endpoint = Deno.env.get("MOADOOM_MAIL_API_URL") ?? "https://api.resend.com/emails";
  const report = await runMailer({
    providerConfigured: Boolean(apiKey && from),
    accountUrl: Deno.env.get("MOADOOM_ACCOUNT_URL") ?? "https://nasawon0-beep.github.io/insurance-ai-updates/account/",
    async enqueueExpiryNotices() {
      const { data, error } = await db.rpc("moadoom_enqueue_expiry_notices");
      if (error) throw error;
      return Number(data ?? 0);
    },
    async claim(batch) {
      const { data, error } = await db.rpc("moadoom_mail_claim", { batch });
      if (error) throw error;
      return (data ?? []) as OutboxRow[];
    },
    async send(msg) {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json", "Idempotency-Key": msg.idempotencyKey },
        body: JSON.stringify({ from, to: [msg.to], subject: msg.subject, text: msg.text, html: msg.html }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body?.id) throw new Error(`provider ${res.status}`);
      return { id: String(body.id) };
    },
    async result(id, ok, error, providerId) {
      const { data, error: e } = await db.rpc("moadoom_mail_result", { mail_id: id, ok, error, provider_id: providerId });
      if (e) throw e;
      return String(data);
    },
  });
  return new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } });
});
