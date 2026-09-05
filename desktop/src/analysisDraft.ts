export function hasPendingDraft(
  pendingPolicies: readonly unknown[],
  pendingCoverage: readonly unknown[],
  pendingConsult: unknown | null,
): boolean {
  return pendingPolicies.length > 0 || pendingCoverage.length > 0 || pendingConsult != null;
}
