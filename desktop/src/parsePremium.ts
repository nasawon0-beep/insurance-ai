// 월납 보험료 입력 문자열 → 숫자|null. "100,000" 처럼 쉼표가 들어와도 파싱하고,
// 숫자로 볼 수 없는 값은 조용히 null 로 삼키지 않고 오류를 던진다.
// (이전: `Number("100,000")` → NaN → JSON.stringify 가 null 로 바꿔 보험료가 말없이 사라졌다.)
export function parsePremium(raw: string): number | null {
  const t = (raw ?? "").trim();
  if (t === "") return null;
  const cleaned = t.replace(/[,\s₩원]/g, "");
  const n = Number(cleaned);
  if (!Number.isFinite(n) || n < 0) {
    throw new Error("월납 보험료는 숫자로 입력해주세요 (예: 100000).");
  }
  return n;
}
