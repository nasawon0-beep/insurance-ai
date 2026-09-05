/** start_date ~ end_date 중 오늘까지 경과 비율(0~100). 계산 불가면 null. */
export function policyProgressPct(
  startDate: string | null,
  endDate: string | null,
  today: Date = new Date(),
): number | null {
  if (!(today instanceof Date) || Number.isNaN(today.getTime())) return null;
  const s = parseYmd(startDate);
  const e = parseYmd(endDate);
  if (!s || !e || e <= s) return null;
  const now = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const pct = ((now - s) / (e - s)) * 100;
  return Math.min(100, Math.max(0, Math.round(pct)));
}

function parseYmd(value: string | null): number | null {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (
    date.getFullYear() !== year
    || date.getMonth() !== month - 1
    || date.getDate() !== day
  ) return null;
  return date.getTime();
}
