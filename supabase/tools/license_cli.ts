// 테스트/운영 보조 CLI (stdin JSON → stdout JSON). 실제 서명·검증 코드(Edge Function과 앱)를 그대로 호출한다.
//   {"op":"keygen","private_out":"<path>"}           → {"kid","public_key"}  (개인키는 파일로만 저장, 출력 안 함)
//   {"op":"sign","ent":{},"user":{},"kid","private_file"} → {"license":{...}}
//   {"op":"verify","license":{},"public_keys":{},"sub","now","offline"} → 검증 결과
import { readFileSync, writeFileSync } from "node:fs";
import { buildLicense, importSigningKey, signLicense } from "../functions/_shared/license.ts";
import { verifyCentralLicense } from "../../desktop/src/licenseToken.ts";

const input = JSON.parse(readFileSync(0, "utf8"));

async function main() {
  if (input.op === "keygen") {
    const kp = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
    const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", kp.privateKey)).toString("base64");
    const raw = Buffer.from(await crypto.subtle.exportKey("raw", kp.publicKey)).toString("base64");
    writeFileSync(input.private_out, pkcs8, { encoding: "utf8", flag: "wx", mode: 0o600 });
    const kid = input.kid ?? `moadoom-${new Date().toISOString().slice(0, 10)}`;
    return { kid, public_key: raw };
  }
  if (input.op === "keygen_env") {
    // 운영 서명키 생성: 개인키는 Edge Function secrets용 env 파일에만 쓴다(화면·로그 출력 없음).
    // 사용: echo {"op":"keygen_env","env_out":"<새 파일>","kid":"moadoom-YYYYMMDD"} | node --experimental-strip-types supabase/tools/license_cli.ts
    // → 출력: {kid, public_key} (공개값만). env 파일은 `supabase secrets set --env-file` 후 정책에 따라 보관/삭제.
    if (!input.env_out || !/^[A-Za-z0-9._-]{3,64}$/.test(String(input.kid ?? ""))) throw new Error("env_out and kid required");
    const kp = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
    const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", kp.privateKey)).toString("base64");
    const raw = Buffer.from(await crypto.subtle.exportKey("raw", kp.publicKey)).toString("base64");
    writeFileSync(input.env_out, `MOADOOM_LICENSE_SIGNING_KEY=${pkcs8}\nMOADOOM_LICENSE_KID=${input.kid}\n`,
      { encoding: "utf8", flag: "wx", mode: 0o600 });
    return { kid: input.kid, public_key: raw };
  }
  if (input.op === "sign") {
    const key = await importSigningKey(readFileSync(input.private_file, "utf8").trim());
    const payload = buildLicense(input.ent, input.user, input.kid, input.now ?? Date.now() / 1000);
    return { license: await signLicense(payload, key) };
  }
  if (input.op === "verify") {
    return verifyCentralLicense(input.license, input.public_keys, input.sub, input.now, {
      offline: !!input.offline,
      clockRolledBack: !!input.clock_rolled_back,
    });
  }
  throw new Error("unknown op");
}

main().then((r) => process.stdout.write(JSON.stringify(r)), (e) => {
  process.stderr.write(String(e?.message ?? e));
  process.exit(1);
});
