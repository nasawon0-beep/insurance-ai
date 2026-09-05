export function normalizeDate(raw: string, opts: { birth?: boolean } = {}): string | null {
  const value = raw.trim();
  if (!value) return "";
  if (!/^[0-9\-./\s]+$/.test(value)) return null;

  let parts = value.split(/[\-./\s]+/).filter(Boolean);
  if (parts.length === 1 && (parts[0].length === 6 || parts[0].length === 8)) {
    const digits = parts[0];
    const yearLength = digits.length - 4;
    parts = [digits.slice(0, yearLength), digits.slice(yearLength, yearLength + 2), digits.slice(yearLength + 2)];
  } else if (parts.length === 2 && parts[1].length === 4) {
    parts = [parts[0], parts[1].slice(0, 2), parts[1].slice(2)];
  }

  if (
    parts.length !== 3
    || ![2, 4].includes(parts[0].length)
    || parts[1].length < 1 || parts[1].length > 2
    || parts[2].length < 1 || parts[2].length > 2
  ) return null;

  let year = Number(parts[0]);
  const month = Number(parts[1]);
  const day = Number(parts[2]);
  if (parts[0].length === 2) {
    year += 2000;
    if (opts.birth && year > new Date().getFullYear()) year -= 100;
  }
  const checked = new Date(year, month - 1, day);
  if (checked.getFullYear() !== year || checked.getMonth() !== month - 1 || checked.getDate() !== day) return null;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
