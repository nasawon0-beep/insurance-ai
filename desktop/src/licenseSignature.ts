export type SignedLicenseBlob = {
  license: unknown;
  signed_at: number;
  signature: string;
};

function compareKeys(a: string, b: string): number {
  const left = Array.from(a, (char) => char.codePointAt(0)!);
  const right = Array.from(b, (char) => char.codePointAt(0)!);
  for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
    if (left[i] !== right[i]) return left[i] - right[i];
  }
  return left.length - right.length;
}

function pythonString(value: string): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (char) =>
    `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

/** Python json.dumps(value, sort_keys=True, separators=(",", ":")) for JSON values. */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return pythonString(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort(compareKeys)
      .map((key) => `${pythonString(key)}:${canonicalJson(object[key])}`)
      .join(",")}}`;
  }
  throw new TypeError("Value is not JSON serializable");
}

function constantTimeHexEqual(expected: string, actual: unknown): boolean {
  const candidate = typeof actual === "string" ? actual : "";
  let difference = expected.length ^ candidate.length;
  for (let i = 0; i < expected.length; i += 1) {
    difference |= expected.charCodeAt(i) ^ (candidate.charCodeAt(i) || 0);
  }
  return difference === 0;
}

export async function verifyLicenseSignature(
  blob: SignedLicenseBlob,
  secret: string,
): Promise<boolean> {
  try {
    const raw = canonicalJson({ license: blob.license, signed_at: blob.signed_at });
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(raw));
    const expected = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    return constantTimeHexEqual(expected, blob.signature);
  } catch {
    return false;
  }
}

export async function verifyLicenseForUser(
  blob: SignedLicenseBlob,
  secret: string,
  currentUserId: unknown,
): Promise<boolean> {
  if (!(await verifyLicenseSignature(blob, secret))) return false;
  const license = blob.license;
  return (
    typeof currentUserId === "string" &&
    typeof license === "object" &&
    license !== null &&
    !Array.isArray(license) &&
    typeof (license as Record<string, unknown>).user_id === "string" &&
    (license as Record<string, unknown>).user_id === currentUserId
  );
}
