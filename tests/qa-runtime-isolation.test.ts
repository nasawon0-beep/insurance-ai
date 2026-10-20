import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("native startup resolves the validated runtime profile before rendering", () => {
  const config = read("desktop/src/config.ts");
  const main = read("desktop/src/main.tsx");
  assert.match(config, /invoke<RuntimeProfile>\("runtime_profile"\)/);
  assert.match(config, /url\.hostname !== "127\.0\.0\.1"/);
  assert.match(config, /if \(profile\.qa && \(!profile\.instanceId \|\| !profile\.profileFingerprint\)\)/);
  assert.ok(main.indexOf("initializeRuntimeProfile()") < main.indexOf("<App />"));
  assert.match(main, /운영 데이터로 전환하지 않고 앱 시작을 중단했습니다/);
});

test("native engine requests and QA updates fail closed", () => {
  const engine = read("desktop/src/engine.ts");
  const updater = read("desktop/src/updater.ts");
  const auth = read("desktop/src/auth.ts");
  const config = read("desktop/src/config.ts");
  const rust = read("desktop/src-tauri/src/lib.rs");
  assert.match(engine, /if \(IS_NATIVE_RUNTIME\) return nativeEngineFetch\(path, init\)/);
  assert.match(engine, /QA local-engine instance identity가 일치하지 않습니다/);
  assert.match(updater, /if \(QA_MODE\) return \{ kind: "unsupported" \}/);
  assert.match(updater, /QA 모드에서는 업데이트 설치가 비활성화됩니다/);
  assert.doesNotMatch(auth, /validate_control_server/);
  assert.match(auth, /invoke<NativeControlResponse>\("control_server_request"/);
  assert.match(rust, /\.header\(QA_INSTANCE_HEADER, instance_id\)/);
  assert.match(rust, /\.header\(QA_BINDING_HEADER, binding\)/);
  assert.match(rust, /tls_built_in_root_certs\(false\)/);
  assert.match(rust, /redirect\(reqwest::redirect::Policy::none\(\)\)/);
  assert.doesNotMatch(rust, /danger_accept_invalid_certs|danger_accept_invalid_hostnames/);
  assert.match(config, /const expectedProtocol = qa \? "https:" : "http:"/);
  assert.match(auth, /nativeControlFetch\("\/health"/);
});

test("QA window is created only by the validated Rust runtime profile", () => {
  const rust = read("desktop/src-tauri/src/lib.rs");
  const local = read("local-engine/main.py");
  const control = read("control-server/main.py");
  const generator = read("scripts/create_qa_profile.py");
  const tauri = JSON.parse(read("desktop/src-tauri/tauri.conf.json"));
  assert.equal(tauri.app.windows, undefined);
  assert.match(rust, /qa_runtime_profile\(/);
  assert.match(rust, /builder\.data_directory\(webview_dir\.clone\(\)\)/);
  assert.match(rust, /if profile\.qa \{[\s\S]*wait_for_local_engine_health/);
  assert.match(rust, /CUSTOMER_DB_KEYFILE_ONLY/);
  assert.match(rust, /기존 engine을 종료하거나 재사용하지 않습니다/);
  assert.match(rust, /WindowEvent::CloseRequested/);
  assert.match(rust, /terminate_managed_sidecars\(app_handle\);\s*app_handle\.exit\(0\)/);
  assert.match(local, /ssl_certfile=cert_file, ssl_keyfile=key_file/);
  assert.match(control, /ssl_certfile=cert_file, ssl_keyfile=key_file/);
  assert.match(generator, /SubjectAlternativeName\(\[x509\.IPAddress\(ipaddress\.ip_address\("127\.0\.0\.1"\)\)\]\)/);
});
