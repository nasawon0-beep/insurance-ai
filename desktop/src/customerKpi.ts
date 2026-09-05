export type PremiumPolicy = { premium: number | null; payment_cycle: string | null; status: string };
export type ExpiryPolicy = { end_date: string | null; status: string };

export function isYearly(cycle: string | null | undefined): boolean {
  const c = String(cycle ?? "").trim().toUpperCase();
  if (c === "YEARLY" || c === "연" || c === "연납" || c === "년납") return true;
  return (c.includes("년") || c.includes("연")) && !c.includes("월");
}
export function isLumpSum(cycle: string | null | undefined): boolean {
  const c = String(cycle ?? "").trim().toUpperCase();
  if (!c) return false;
  return c.includes("일시") || c.includes("LUMP") || c.includes("ONE_TIME") || c.includes("ONETIME") || c.includes("SINGLE");
}
export function totalMonthlyPremium(policies: PremiumPolicy[]): { total: number; lumpSumCount: number } {
  let total = 0, lumpSumCount = 0;
  for (const p of policies) {
    if (p.status !== "ACTIVE") continue;
    if (isLumpSum(p.payment_cycle)) { lumpSumCount++; continue; }
    const prem = typeof p.premium === "number" && Number.isFinite(p.premium) ? p.premium : 0;
    if (prem <= 0) continue;
    total += isYearly(p.payment_cycle) ? Math.floor(prem / 12) : prem;
  }
  return { total, lumpSumCount };
}
export function daysUntil(endDate: string, today: Date): number | null {
  const s = endDate.slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return null;
  const [, y, mo, d] = m;
  const end = new Date(Number(y), Number(mo) - 1, Number(d));
  if (end.getFullYear() !== Number(y) || end.getMonth() !== Number(mo) - 1 || end.getDate() !== Number(d)) {
    return null;
  }
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.round((end.getTime() - base.getTime()) / 86400000);
}
export function expiringCount(policies: ExpiryPolicy[], days = 30, today: Date = new Date()): number {
  let n = 0;
  for (const p of policies) {
    if (p.status !== "ACTIVE") continue;
    if (!p.end_date) continue;
    const d = daysUntil(p.end_date, today);
    if (d == null) continue;
    if (d >= 0 && d <= days) n++;
  }
  return n;
}
