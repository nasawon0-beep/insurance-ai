export function withoutCustomerId<T extends { id: string }>(rows: T[], deletedId: string): T[] {
  return rows.filter((row) => row.id !== deletedId);
}