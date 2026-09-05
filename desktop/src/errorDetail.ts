const FIELD_NAMES: Record<string, string> = {
  name: "이름",
  phone: "전화번호",
  rrn: "주민등록번호",
  birth_date: "생년월일",
  gender: "성별",
  email: "이메일",
  address: "주소",
  occupation: "직업",
  tags: "태그",
  memo: "메모",
  first_registered_ym: "최초 등록일",
  insurer: "보험사",
  product_name: "상품명",
  policy_number: "증권번호",
  premium: "보험료",
  consulted_at: "상담일",
  channel: "상담 채널",
  title: "제목",
  content: "내용",
  follow_up_at: "후속 연락일",
  email_address: "이메일",
  password: "비밀번호",
};

export function formatErrorDetail(detail: unknown): string | null {
  if (Array.isArray(detail)) {
    if (detail.length === 0) return null;
    return detail
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const error = item as { loc?: unknown; msg?: unknown };
        const loc = Array.isArray(error.loc) ? error.loc[error.loc.length - 1] : undefined;
        const field = loc == null ? null : FIELD_NAMES[String(loc)] ?? String(loc);
        const message = error.msg == null ? String(item) : String(error.msg);
        return field ? `${field}: ${message}` : message;
      })
      .join(" / ");
  }
  if (detail && typeof detail === "object") {
    const error = detail as { message?: unknown; msg?: unknown };
    if (typeof error.message === "string") return error.message;
    if (typeof error.msg === "string") return error.msg;
    return null;
  }
  return detail == null ? null : String(detail);
}
