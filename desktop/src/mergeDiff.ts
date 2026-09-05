export type MergeRow = { key: string; label: string; before: string; after: string };

const FIELDS: [string, string][] = [
  ["name", "이름"], ["phone", "전화"], ["birth_date", "생년월일"], ["gender", "성별"],
  ["email", "이메일"], ["address", "주소"], ["occupation", "직업"],
];

/** 병합 시 바뀔 고객 필드 행 목록. 새 값이 비었거나 기존과 같으면 제외. */
export function computeMergeRows(
  form: Record<string, string>,
  existing: Record<string, unknown>,
): MergeRow[] {
  const rows: MergeRow[] = [];
  for (const [key, label] of FIELDS) {
    const after = (form[key] ?? "").trim();
    const before = String(existing[key] ?? "");
    if (after && after !== before) rows.push({ key, label, before, after });
  }
  return rows;
}

/** memo append 문자열. 이미 그 내용이 있으면 null. */
export function memoAppend(
  formMemo: string,
  existingMemo: unknown,
  today: string,
): string | null {
  const v = (formMemo ?? "").trim();
  const prev = String(existingMemo ?? "");
  if (!v || prev.includes(v)) return null;
  return (prev ? prev + "\n" : "") + `[${today}] ${v}`;
}
