export type CoverageLinkItem = {
  matched_policy_ids?: string[];
  matched_policy_coverage_ids?: string[];
};

export function resolveMatchedPolicyCoverages<T extends { id: string }>(
  item: CoverageLinkItem,
  policyCoverageMap: ReadonlyMap<string, T>,
): T[] {
  const ids = Array.isArray(item.matched_policy_coverage_ids)
    ? item.matched_policy_coverage_ids
    : [];
  return ids
    .map((id) => policyCoverageMap.get(id))
    .filter((coverage): coverage is T => coverage !== undefined);
}
