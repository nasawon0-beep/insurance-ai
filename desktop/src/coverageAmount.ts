export function coverageAmountLabel(
  amount: number | null | undefined,
  amountStatus?: string | null,
  amountText?: string | null,
): string {
  if (["unknown", "partial_unknown", "unknown_dash", "unknown_blank", "parse_failed"].includes(amountStatus ?? "")) {
    return "금액 확인필요";
  }
  if (amountStatus === "explicit_zero") return "0원 (원문 명시)";
  if (typeof amount === "number") return `${amount.toLocaleString("ko-KR")}원`;
  return amountText?.trim() || "금액 확인필요";
}
