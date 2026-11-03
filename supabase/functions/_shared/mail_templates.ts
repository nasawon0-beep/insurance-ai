// 모아둠 안내 메일 템플릿(정보성 안내만 — 광고/마케팅 문구 없음).
// 가입 확인·비밀번호 재설정 메일은 Supabase Auth가 보낸다(supabase/templates/*.html, 대시보드 Email Templates에 붙여넣기).
// 여기는 이용권 안내: 무료체험 시작, 이용권 변경, 종료 전 안내.

export type MailKind = "welcome_trial" | "entitlement_changed" | "expiry_notice";
export type RenderedMail = { subject: string; text: string; html: string };

const SITE = "모아둠";

function kst(iso: unknown): string {
  if (typeof iso !== "string") return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  return new Date(t + 9 * 3600 * 1000).toISOString().slice(0, 16).replace("T", " ");
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

const KIND_LABEL: Record<string, string> = { trial: "무료체험", term: "기간 이용", lifetime: "무기한 무료" };
const ACTION_LABEL: Record<string, string> = {
  trial: "무료체험 기간이 지정되었습니다",
  term: "이용 기간이 지정되었습니다",
  lifetime: "무기한 무료 이용이 지정되었습니다",
  adjust: "이용 기간이 변경되었습니다",
  suspend: "이용이 정지되었습니다",
  resume: "이용 정지가 해제되었습니다",
};

function wrap(title: string, lines: string[], accountUrl: string): RenderedMail & { _lines: string[] } {
  const footer = [
    "이 메일은 모아둠 계정의 이용 상태 안내를 위해 보내는 정보성 메일입니다.",
    "고객 자료는 회원님 PC에만 있으며 이 메일과 서버에는 포함되지 않습니다.",
  ];
  const text = [title, "", ...lines, "", `내 계정: ${accountUrl}`, "", ...footer].join("\n");
  const html = `<!doctype html><html lang="ko"><body style="margin:0;background:#fbfcfa;font-family:'Malgun Gothic',sans-serif;color:#40514b">
<div style="max-width:520px;margin:24px auto;background:#fff;border:1px solid #d5dfda;border-radius:10px;padding:24px">
<h1 style="font-size:18px;color:#17231f;margin:0 0 14px">${esc(title)}</h1>
${lines.map((l) => `<p style="margin:0 0 8px;font-size:14px;line-height:1.6">${esc(l)}</p>`).join("\n")}
<p style="margin:18px 0"><a href="${esc(accountUrl)}" style="background:#087b6b;color:#fff;padding:9px 14px;border-radius:7px;text-decoration:none;font-size:14px">내 계정 보기</a></p>
${footer.map((l) => `<p style="margin:0;font-size:12px;color:#697871">${esc(l)}</p>`).join("\n")}
</div></body></html>`;
  return { subject: `[${SITE}] ${title}`, text, html, _lines: lines };
}

export function renderMail(kind: MailKind, payload: Record<string, any>, accountUrl: string): RenderedMail {
  let r;
  if (kind === "welcome_trial") {
    r = wrap("무료체험 7일이 시작되었습니다", [
      "이메일 확인이 끝나 모아둠 무료체험이 시작되었습니다.",
      `시작: ${kst(payload.starts_at)} / 종료: ${kst(payload.ends_at)} (한국 시간)`,
      "PC 앱에서 같은 이메일로 로그인하면 바로 사용할 수 있습니다.",
      "무료체험은 계정당 1회입니다. 자동 결제는 없습니다.",
    ], accountUrl);
  } else if (kind === "entitlement_changed") {
    const st = payload.state ?? {};
    const lines = [ACTION_LABEL[payload.action] ?? "이용 상태가 변경되었습니다", `현재 이용 형태: ${KIND_LABEL[st.kind] ?? "-"}`];
    if (st.kind === "lifetime") lines.push("종료일 없이 사용할 수 있습니다.");
    else if (st.ends_at) lines.push(`종료: ${kst(st.ends_at)} (한국 시간)`);
    if (st.status === "suspended") lines.push("이용이 정지된 상태입니다. 고객 자료는 회원님 PC에 그대로 있습니다.");
    r = wrap("모아둠 이용 상태가 변경되었습니다", lines, accountUrl);
  } else {
    const what = payload.kind === "trial" ? "무료체험" : "이용 기간";
    r = wrap(`${what} 종료 ${payload.days_before}일 전 안내`, [
      `${what}이 ${kst(payload.ends_at)}(한국 시간)에 끝납니다.`,
      "종료 후에도 고객 자료는 회원님 PC에 그대로 있습니다.",
      "계속 사용을 원하시면 모아둠 관리자에게 문의해 주세요. 자동 결제는 없습니다.",
    ], accountUrl);
  }
  return { subject: r.subject, text: r.text, html: r.html };
}
