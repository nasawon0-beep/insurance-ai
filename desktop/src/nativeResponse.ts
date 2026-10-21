const NULL_BODY_STATUSES = new Set([204, 205, 304]);

/** Rebuild a Fetch Response returned by a Tauri bridge without violating
 * the Fetch rule that null-body statuses must receive a null body. */
export function responseFromNativeResult(result: {
  status: number;
  body: number[];
  headers: [string, string][];
}): Response {
  const body = NULL_BODY_STATUSES.has(result.status) ? null : new Uint8Array(result.body);
  return new Response(body, {
    status: result.status,
    headers: result.headers,
  });
}