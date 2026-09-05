export type CoverageRow = {
  name: string;
  status: "미가입" | "부족" | "충분" | string;
  pct: number;
  current: string | null;
  recommended: string | null;
};

export function normalizeCoverageRows(rows: unknown[]): CoverageRow[] {
  return (Array.isArray(rows) ? rows : [])
    .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
    .map((row) => {
      const name = String(row.name ?? "").trim();
      const status = String(row.status ?? "").trim();
      const pct = Number(row.pct);
      const current = typeof row.current === "string" ? row.current.trim() : "";
      const recommended = typeof row.recommended === "string" ? row.recommended.trim() : "";
      return {
        name,
        status: status || "미가입",
        pct: Math.min(100, Math.max(0, Number.isFinite(pct) ? pct : 0)),
        current: current || null,
        recommended: recommended || null,
      } as CoverageRow;
    })
    .filter((row) => !(row.name === "" && row.current == null && row.recommended == null));
}
