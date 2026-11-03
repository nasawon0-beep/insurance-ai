// Supabase Edge Function: moadoom-license
// 호출자(로그인 회원)의 JWT로 본인 이용권만 조회해 Ed25519로 서명한 토큰을 돌려준다.
// - service_role/secret 키를 쓰지 않는다: 사용자 JWT로 RPC(moadoom_my_entitlement)를 호출하므로 RLS/auth.uid()가 그대로 적용된다.
// - 필요한 secret: MOADOOM_LICENSE_SIGNING_KEY(PKCS8 base64), MOADOOM_LICENSE_KID
// - 보험 고객 자료는 받지도 저장하지도 않는다.
import { createClient } from "jsr:@supabase/supabase-js@2";
import { buildLicense, type Entitlement, importSigningKey, signLicense } from "../_shared/license.ts";

const ALLOWED_ORIGINS = new Set(
  (Deno.env.get("MOADOOM_ALLOWED_ORIGINS") ??
    "tauri://localhost,http://tauri.localhost,https://tauri.localhost,https://nasawon0-beep.github.io")
    .split(",").map((s) => s.trim()).filter(Boolean),
);

function cors(req: Request): Record<string, string> {
  const origin = req.headers.get("Origin") ?? "";
  return ALLOWED_ORIGINS.has(origin)
    ? {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      Vary: "Origin",
    }
    : { Vary: "Origin" };
}

function json(req: Request, status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...cors(req) },
  });
}

function publishableKey(): string {
  const legacy = Deno.env.get("SUPABASE_ANON_KEY");
  if (legacy) return legacy;
  const keys = JSON.parse(Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") ?? "{}") as Record<string, string>;
  const first = Object.values(keys)[0];
  if (!first) throw new Error("publishable key unavailable");
  return first;
}

let signingKey: Promise<CryptoKey> | null = null;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(req) });
  if (req.method !== "POST") return json(req, 405, { error: "method_not_allowed" });
  const authorization = req.headers.get("Authorization") ?? "";
  const token = authorization.toLowerCase().startsWith("bearer ") ? authorization.slice(7) : "";
  if (!token) return json(req, 401, { error: "login_required" });

  const client = createClient(Deno.env.get("SUPABASE_URL")!, publishableKey(), {
    global: { headers: { Authorization: `Bearer ${token}` } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await client.auth.getUser(token);
  if (userError || !userData.user?.email) return json(req, 401, { error: "login_required" });
  if (!userData.user.email_confirmed_at) return json(req, 403, { error: "email_not_confirmed" });

  const { data: ent, error: entError } = await client.rpc("moadoom_my_entitlement");
  if (entError || !ent) return json(req, 502, { error: "entitlement_unavailable" });

  const kid = Deno.env.get("MOADOOM_LICENSE_KID");
  const pkcs8 = Deno.env.get("MOADOOM_LICENSE_SIGNING_KEY");
  if (!kid || !pkcs8) return json(req, 503, { error: "signing_not_configured" });
  signingKey ??= importSigningKey(pkcs8);
  const payload = buildLicense(ent as Entitlement, { id: userData.user.id, email: userData.user.email }, kid,
    Date.now() / 1000);
  return json(req, 200, { license: await signLicense(payload, await signingKey) });
});
