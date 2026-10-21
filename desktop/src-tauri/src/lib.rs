mod ollama;

use fs2::FileExt;
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

#[derive(serde::Deserialize)]
struct LocalEngineRequest {
    method: String,
    path: String,
    headers: Vec<(String, String)>,
    body: Option<LocalEngineRequestBody>,
}

#[derive(serde::Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
enum LocalEngineRequestBody {
    Text { text: String },
    Bytes { bytes: Vec<u8> },
    FormData { fields: Vec<LocalEngineFormField> },
}

#[derive(serde::Deserialize)]
struct LocalEngineFormField {
    name: String,
    value: Option<String>,
    file_name: Option<String>,
    content_type: Option<String>,
    bytes: Option<Vec<u8>>,
}

#[derive(serde::Serialize)]
struct LocalEngineResponse {
    status: u16,
    body: Vec<u8>,
    headers: Vec<(String, String)>,
}

struct SidecarChildren {
    control_server: Mutex<Option<CommandChild>>,
    local_engine: Mutex<Option<CommandChild>>,
}

// 앱 수명 동안 flock(LOCK_EX) 을 잡고 있는 파일. 앱이 어떤 이유로든 죽으면 OS 가 락을
// 해제하고, 사이드카가 LOCK_NB 로 잡히는 걸 보고 자체 종료한다(PID 재사용·좀비 무관).
struct AppLock(#[allow(dead_code)] File);

#[cfg(windows)]
struct SidecarJob(Mutex<Option<isize>>);

#[cfg(windows)]
impl SidecarJob {
    fn new() -> Result<Self, Box<dyn std::error::Error>> {
        use std::mem::size_of;
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };

        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(std::io::Error::last_os_error().into());
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                let error = std::io::Error::last_os_error();
                windows_sys::Win32::Foundation::CloseHandle(handle);
                return Err(error.into());
            }
            Ok(Self(Mutex::new(Some(handle as isize))))
        }
    }

    fn assign(&self, pid: u32) -> Result<(), Box<dyn std::error::Error>> {
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
        };

        let job = self.0.lock().expect("sidecar job lock");
        let job = (*job).ok_or("sidecar Job Object가 이미 닫혔습니다")? as *mut _;
        unsafe {
            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
            if process.is_null() {
                return Err(std::io::Error::last_os_error().into());
            }
            let assigned = AssignProcessToJobObject(job, process);
            windows_sys::Win32::Foundation::CloseHandle(process);
            if assigned == 0 {
                return Err(std::io::Error::last_os_error().into());
            }
        }
        Ok(())
    }

    fn terminate_all(&self) {
        if let Some(handle) = self.0.lock().expect("sidecar job lock").take() {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(handle as *mut _);
            }
        }
    }
}

#[cfg(windows)]
impl Drop for SidecarJob {
    fn drop(&mut self) {
        self.terminate_all();
    }
}

fn random_hex() -> Result<String, Box<dyn std::error::Error>> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|e| format!("CSPRNG 실패: {e}"))?; // OS CSPRNG (크로스플랫폼)
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn load_or_create_secrets(path: &Path) -> Result<(String, String), Box<dyn std::error::Error>> {
    if !path.exists() {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let jwt = random_hex()?;
        let admin = random_hex()?;
        let contents = format!("CONTROL_JWT_SECRET={jwt}\nCONTROL_ADMIN_TOKEN={admin}\n");
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options.open(path)?.write_all(contents.as_bytes())?;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }

    let contents = fs::read_to_string(path)?;
    let value = |key: &str| {
        contents
            .lines()
            .filter_map(|line| line.split_once('='))
            .find_map(|(name, value)| (name.trim() == key).then(|| value.trim().to_owned()))
            .filter(|value| !value.is_empty())
    };
    let jwt =
        value("CONTROL_JWT_SECRET").ok_or("control-secrets.env에 CONTROL_JWT_SECRET이 없습니다")?;
    let admin = value("CONTROL_ADMIN_TOKEN")
        .ok_or("control-secrets.env에 CONTROL_ADMIN_TOKEN이 없습니다")?;
    Ok((jwt, admin))
}

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

const LOCAL_ENGINE_HOST: &str = "127.0.0.1";
const LOCAL_ENGINE_PORT: u16 = 8420;
const LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT: Duration = Duration::from_millis(1500);
const LOCAL_ENGINE_PORT_RELEASE_TIMEOUT: Duration = Duration::from_secs(5);
const LOCAL_ENGINE_STARTUP_MAX_ATTEMPTS: usize = 10;
const LOCAL_ENGINE_STARTUP_RETRY_INTERVAL: Duration = Duration::from_secs(2);
const CONTROL_SERVER_PORT: u16 = 8790;
const QA_MARKER_NAME: &str = ".insurance-ai-qa-profile.json";
const QA_INSTANCE_HEADER: &str = "X-Insurance-AI-QA-Instance";
const QA_BINDING_HEADER: &str = "X-Insurance-AI-QA-Binding";

#[derive(Clone)]
pub(crate) struct RuntimeProfile {
    pub(crate) qa: bool,
    data_dir: PathBuf,
    config_dir: PathBuf,
    webview_dir: Option<PathBuf>,
    log_dir: PathBuf,
    model_dir: PathBuf,
    engine_port: u16,
    control_port: u16,
    instance_id: Option<String>,
    profile_fingerprint: Option<String>,
    qa_tls_cert_pem: Option<Vec<u8>>,
    qa_tls_cert_path: Option<PathBuf>,
    qa_tls_key_path: Option<PathBuf>,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeProfileInfo {
    qa: bool,
    local_engine_url: String,
    control_url: String,
    instance_id: Option<String>,
    profile_fingerprint: Option<String>,
}

impl RuntimeProfile {
    fn info(&self) -> RuntimeProfileInfo {
        RuntimeProfileInfo {
            qa: self.qa,
            local_engine_url: format!(
                "{}://{LOCAL_ENGINE_HOST}:{}",
                if self.qa { "https" } else { "http" },
                self.engine_port
            ),
            control_url: format!(
                "{}://{LOCAL_ENGINE_HOST}:{}",
                if self.qa { "https" } else { "http" },
                self.control_port
            ),
            instance_id: self.instance_id.clone(),
            profile_fingerprint: self.profile_fingerprint.clone(),
        }
    }

    fn lock_path(&self) -> PathBuf {
        if self.qa {
            self.data_dir.join("runtime").join("app.lock")
        } else {
            self.data_dir.join("app.lock")
        }
    }

    fn engine_secret_path(&self) -> PathBuf {
        if self.qa {
            self.data_dir
                .join("runtime")
                .join("local-engine-api.secret")
        } else {
            self.data_dir.join("local-engine-api.secret")
        }
    }

    fn customer_key_path(&self) -> PathBuf {
        self.config_dir.join("customer-db.key")
    }

    fn control_secrets_path(&self) -> PathBuf {
        self.config_dir.join("control-secrets.env")
    }
}

#[derive(serde::Deserialize)]
struct QaProfileMarker {
    schema: u8,
    instance_id: String,
    profile_fingerprint: String,
    transport: String,
}

#[derive(Default)]
struct QaLaunchArgs {
    root: Option<PathBuf>,
    instance_id: Option<String>,
    engine_port: Option<u16>,
    control_port: Option<u16>,
    saw_qa_arg: bool,
}

fn parse_qa_launch_args(
    args: impl IntoIterator<Item = OsString>,
) -> Result<Option<QaLaunchArgs>, String> {
    let mut parsed = QaLaunchArgs::default();
    let mut iter = args.into_iter().skip(1);
    while let Some(arg) = iter.next() {
        let arg = arg.to_string_lossy();
        let target = match arg.as_ref() {
            "--qa-profile-dir" => "root",
            "--qa-instance-id" => "instance",
            "--qa-engine-port" => "engine",
            "--qa-control-port" => "control",
            value if value.starts_with("--qa-") => {
                return Err(format!("알 수 없는 QA 실행 인자입니다: {value}"));
            }
            _ => continue,
        };
        parsed.saw_qa_arg = true;
        let value = iter.next().ok_or_else(|| format!("{arg} 값이 없습니다"))?;
        match target {
            "root" => {
                if parsed.root.replace(PathBuf::from(value)).is_some() {
                    return Err("--qa-profile-dir가 중복되었습니다".to_owned());
                }
            }
            "instance" => {
                if parsed
                    .instance_id
                    .replace(value.to_string_lossy().into_owned())
                    .is_some()
                {
                    return Err("--qa-instance-id가 중복되었습니다".to_owned());
                }
            }
            "engine" => {
                let port = value
                    .to_string_lossy()
                    .parse::<u16>()
                    .map_err(|_| "--qa-engine-port는 유효한 포트여야 합니다".to_owned())?;
                if parsed.engine_port.replace(port).is_some() {
                    return Err("--qa-engine-port가 중복되었습니다".to_owned());
                }
            }
            _ => {
                let port = value
                    .to_string_lossy()
                    .parse::<u16>()
                    .map_err(|_| "--qa-control-port는 유효한 포트여야 합니다".to_owned())?;
                if parsed.control_port.replace(port).is_some() {
                    return Err("--qa-control-port가 중복되었습니다".to_owned());
                }
            }
        }
    }
    if !parsed.saw_qa_arg {
        return Ok(None);
    }
    if parsed.root.is_none()
        || parsed.instance_id.is_none()
        || parsed.engine_port.is_none()
        || parsed.control_port.is_none()
    {
        return Err(
            "QA 실행은 profile-dir, instance-id, engine-port, control-port를 모두 명시해야 합니다"
                .to_owned(),
        );
    }
    Ok(Some(parsed))
}

fn is_uuid_like(value: &str) -> bool {
    value.len() == 36
        && value.chars().enumerate().all(|(index, ch)| {
            matches!(index, 8 | 13 | 18 | 23) && ch == '-'
                || !matches!(index, 8 | 13 | 18 | 23) && ch.is_ascii_hexdigit()
        })
}

fn is_fingerprint(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|ch| ch.is_ascii_hexdigit())
}

#[cfg(windows)]
fn is_absolute_local_path(path: &Path) -> bool {
    use std::path::{Component, Prefix};
    matches!(
        path.components().next(),
        Some(Component::Prefix(prefix))
            if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_))
    ) && path.is_absolute()
}

#[cfg(not(windows))]
fn is_absolute_local_path(path: &Path) -> bool {
    path.is_absolute()
}

fn has_link_or_reparse_component(path: &Path) -> Result<bool, String> {
    for component in path.ancestors() {
        if component.as_os_str().is_empty() || !component.exists() {
            continue;
        }
        let metadata = fs::symlink_metadata(component)
            .map_err(|e| format!("QA 경로 metadata 확인 실패({}): {e}", component.display()))?;
        if metadata.file_type().is_symlink() {
            return Ok(true);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

fn paths_overlap(left: &Path, right: &Path) -> bool {
    left == right || left.starts_with(right) || right.starts_with(left)
}

fn validate_qa_ports(engine_port: u16, control_port: u16) -> Result<(), String> {
    if engine_port < 1024 || control_port < 1024 {
        return Err("QA 포트는 1024 이상이어야 합니다".to_owned());
    }
    if engine_port == LOCAL_ENGINE_PORT || control_port == CONTROL_SERVER_PORT {
        return Err("QA 포트는 production 포트 8420/8790을 사용할 수 없습니다".to_owned());
    }
    if engine_port == control_port {
        return Err("QA engine/control 포트는 서로 달라야 합니다".to_owned());
    }
    Ok(())
}

fn ensure_ports_available(ports: &[u16]) -> Result<(), String> {
    let mut guards = Vec::new();
    for port in ports {
        let listener = TcpListener::bind((LOCAL_ENGINE_HOST, *port)).map_err(|_| {
            format!("QA 포트 {port}가 이미 사용 중입니다. 기존 프로세스를 재사용하거나 종료하지 않습니다")
        })?;
        guards.push(listener);
    }
    drop(guards);
    Ok(())
}

fn qa_runtime_profile(
    args: QaLaunchArgs,
    production_data_dir: &Path,
    production_config_dir: &Path,
) -> Result<RuntimeProfile, String> {
    let requested_root = args.root.ok_or("QA root가 없습니다")?;
    if !is_absolute_local_path(&requested_root) {
        return Err("QA root는 UNC가 아닌 절대 local 경로여야 합니다".to_owned());
    }
    if requested_root
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("QA root에는 '..'를 사용할 수 없습니다".to_owned());
    }
    if !requested_root.is_dir() {
        return Err("QA root는 marker를 포함해 미리 생성된 디렉터리여야 합니다".to_owned());
    }
    if has_link_or_reparse_component(&requested_root)? {
        return Err(
            "QA root 또는 상위 경로에 symlink/junction/reparse point가 있습니다".to_owned(),
        );
    }
    let root = requested_root
        .canonicalize()
        .map_err(|e| format!("QA root canonicalize 실패: {e}"))?;
    for production in [production_data_dir, production_config_dir] {
        let normalized = production
            .canonicalize()
            .unwrap_or_else(|_| production.to_path_buf());
        if paths_overlap(&root, &normalized) {
            return Err("QA root가 production data/config 경로와 겹칩니다".to_owned());
        }
    }
    let marker_path = root.join(QA_MARKER_NAME);
    if has_link_or_reparse_component(&marker_path)? {
        return Err("QA marker는 symlink/reparse file일 수 없습니다".to_owned());
    }
    let marker: QaProfileMarker = serde_json::from_slice(
        &fs::read(&marker_path).map_err(|e| format!("QA marker 읽기 실패: {e}"))?,
    )
    .map_err(|e| format!("QA marker JSON 오류: {e}"))?;
    if marker.schema != 2 || marker.transport != "pinned-tls-v1" {
        return Err("지원하지 않는 QA marker schema입니다".to_owned());
    }
    let instance_id = args.instance_id.ok_or("QA instance id가 없습니다")?;
    if !is_uuid_like(&instance_id) || marker.instance_id != instance_id {
        return Err("QA marker instance_id가 실행 인자와 일치하지 않습니다".to_owned());
    }
    if !is_fingerprint(&marker.profile_fingerprint) {
        return Err("QA marker fingerprint가 올바르지 않습니다".to_owned());
    }
    let engine_port = args.engine_port.ok_or("QA engine port가 없습니다")?;
    let control_port = args.control_port.ok_or("QA control port가 없습니다")?;
    validate_qa_ports(engine_port, control_port)?;
    ensure_ports_available(&[engine_port, control_port])?;
    let tls_cert_path = root.join("config").join("qa-loopback-cert.pem");
    let tls_key_path = root.join("config").join("qa-loopback-key.pem");
    for (path, label) in [
        (&tls_cert_path, "certificate"),
        (&tls_key_path, "private key"),
    ] {
        if has_link_or_reparse_component(path)? {
            return Err(format!(
                "QA TLS {label}는 symlink/reparse file일 수 없습니다"
            ));
        }
        let metadata =
            fs::metadata(path).map_err(|e| format!("QA TLS {label} metadata 확인 실패: {e}"))?;
        if !metadata.is_file() {
            return Err(format!("QA TLS {label}가 regular file이 아닙니다"));
        }
        let canonical = path
            .canonicalize()
            .map_err(|e| format!("QA TLS {label} canonicalize 실패: {e}"))?;
        if !canonical.starts_with(&root) {
            return Err(format!("QA TLS {label}가 QA root 밖을 가리킵니다"));
        }
    }
    let tls_cert_pem =
        fs::read(&tls_cert_path).map_err(|e| format!("QA TLS certificate 읽기 실패: {e}"))?;
    reqwest::Certificate::from_pem(&tls_cert_pem)
        .map_err(|e| format!("QA TLS certificate PEM 오류: {e}"))?;

    Ok(RuntimeProfile {
        qa: true,
        data_dir: root.join("data"),
        config_dir: root.join("config"),
        webview_dir: Some(root.join("webview2")),
        log_dir: root.join("logs"),
        model_dir: root.join("models"),
        engine_port,
        control_port,
        instance_id: Some(instance_id),
        profile_fingerprint: Some(marker.profile_fingerprint),
        qa_tls_cert_pem: Some(tls_cert_pem),
        qa_tls_cert_path: Some(tls_cert_path),
        qa_tls_key_path: Some(tls_key_path),
    })
}

fn production_runtime_profile(config_dir: PathBuf, data_dir: PathBuf) -> RuntimeProfile {
    RuntimeProfile {
        qa: false,
        log_dir: local_engine_log_path_for(&data_dir)
            .parent()
            .unwrap_or(&data_dir)
            .to_path_buf(),
        model_dir: data_dir.join("whisper-models"),
        data_dir,
        config_dir,
        webview_dir: None,
        engine_port: LOCAL_ENGINE_PORT,
        control_port: CONTROL_SERVER_PORT,
        instance_id: None,
        profile_fingerprint: None,
        qa_tls_cert_pem: None,
        qa_tls_cert_path: None,
        qa_tls_key_path: None,
    }
}

fn local_engine_log_path_for(data_dir: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        if let Some(appdata) = std::env::var_os("APPDATA") {
            return PathBuf::from(appdata)
                .join("Insurance AI")
                .join("logs")
                .join("local-engine.log");
        }
    }
    data_dir.join("logs").join("local-engine.log")
}

fn sidecar_log_path(profile: &RuntimeProfile, file_name: &str) -> PathBuf {
    if profile.qa {
        profile.log_dir.join(file_name)
    } else if file_name == "local-engine.log" {
        local_engine_log_path_for(&profile.data_dir)
    } else {
        profile.log_dir.join(file_name)
    }
}

fn append_local_engine_log(log_path: &Path, line: &str) {
    if let Some(parent) = log_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = writeln!(file, "{line}");
    }
}

#[tauri::command]
fn local_engine_log_path(profile: tauri::State<'_, RuntimeProfile>) -> Result<String, String> {
    Ok(sidecar_log_path(&profile, "local-engine.log")
        .to_string_lossy()
        .into_owned())
}

fn local_engine_is_healthy_at(host: &str, port: u16, timeout: Duration) -> bool {
    health_response_at(host, port, timeout)
        .and_then(|body| {
            body.get("local_engine")
                .and_then(|value| value.as_str())
                .map(str::to_owned)
        })
        .as_deref()
        == Some("ok")
}

fn health_response_at(host: &str, port: u16, timeout: Duration) -> Option<serde_json::Value> {
    let Ok(mut addrs) = (host, port).to_socket_addrs() else {
        return None;
    };
    let Some(addr) = addrs.next() else {
        return None;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return None;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!(
        "GET /health HTTP/1.1\x0d\x0aHost: {host}:{port}\x0d\x0aConnection: close\x0d\x0a\x0d\x0a"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return None;
    }
    let deadline = Instant::now() + timeout;
    let mut response = Vec::new();
    let mut buf = [0_u8; 512];
    while Instant::now() < deadline {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => response.extend_from_slice(&buf[..n]),
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                break;
            }
            Err(_) => return None,
        }
    }
    let response = String::from_utf8_lossy(&response);
    if !(response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")) {
        return None;
    }
    let body = response.split_once("\x0d\x0a\x0d\x0a")?.1;
    serde_json::from_str(body).ok()
}

fn qa_pinned_certificate(profile: &RuntimeProfile) -> Result<reqwest::Certificate, String> {
    let pem = profile
        .qa_tls_cert_pem
        .as_deref()
        .ok_or_else(|| "QA pinned TLS certificate가 없습니다".to_owned())?;
    reqwest::Certificate::from_pem(pem).map_err(|e| format!("QA pinned TLS certificate 오류: {e}"))
}

fn qa_health_response_at(
    host: &str,
    port: u16,
    profile: &RuntimeProfile,
    timeout: Duration,
) -> Option<serde_json::Value> {
    let certificate = qa_pinned_certificate(profile).ok()?;
    let client = reqwest::blocking::Client::builder()
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::none())
        .tls_built_in_root_certs(false)
        .add_root_certificate(certificate)
        .build()
        .ok()?;
    let response = client
        .get(format!("https://{host}:{port}/health"))
        .send()
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    serde_json::from_str(&response.text().ok()?).ok()
}

fn health_matches_profile_at(
    host: &str,
    port: u16,
    service_key: &str,
    profile: &RuntimeProfile,
    timeout: Duration,
) -> bool {
    let body = if profile.qa {
        qa_health_response_at(host, port, profile, timeout)
    } else {
        health_response_at(host, port, timeout)
    };
    let Some(body) = body else {
        return false;
    };
    health_body_matches_profile(&body, service_key, profile)
}

fn health_body_matches_profile(
    body: &serde_json::Value,
    service_key: &str,
    profile: &RuntimeProfile,
) -> bool {
    if body.get(service_key).and_then(|value| value.as_str()) != Some("ok") {
        return false;
    }
    if !profile.qa {
        return true;
    }
    body.get("instance_id").and_then(|value| value.as_str()) == profile.instance_id.as_deref()
        && body
            .get("profile_fingerprint")
            .and_then(|value| value.as_str())
            == profile.profile_fingerprint.as_deref()
}

fn is_tcp_port_open(host: &str, port: u16, timeout: Duration) -> bool {
    let Ok(mut addrs) = (host, port).to_socket_addrs() else {
        return false;
    };
    let Some(addr) = addrs.next() else {
        return false;
    };
    TcpStream::connect_timeout(&addr, timeout).is_ok()
}

fn wait_for_local_engine_port_released(port: u16, total_timeout: Duration) -> bool {
    wait_for_tcp_port_released(LOCAL_ENGINE_HOST, port, total_timeout)
}

fn wait_for_tcp_port_released(host: &str, port: u16, total_timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < total_timeout {
        if !is_tcp_port_open(host, port, Duration::from_millis(200)) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    !is_tcp_port_open(host, port, Duration::from_millis(200))
}

fn wait_for_local_engine_health(profile: &RuntimeProfile) -> bool {
    wait_for_profile_service_health(profile, "local_engine", profile.engine_port)
}

fn wait_for_profile_service_health(profile: &RuntimeProfile, service_key: &str, port: u16) -> bool {
    for attempt in 1..=LOCAL_ENGINE_STARTUP_MAX_ATTEMPTS {
        if health_matches_profile_at(
            LOCAL_ENGINE_HOST,
            port,
            service_key,
            profile,
            LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT,
        ) {
            return true;
        }
        if attempt < LOCAL_ENGINE_STARTUP_MAX_ATTEMPTS {
            std::thread::sleep(LOCAL_ENGINE_STARTUP_RETRY_INTERVAL);
        }
    }
    false
}

#[cfg(windows)]
fn kill_unhealthy_local_engine_processes() -> Result<(), Box<dyn std::error::Error>> {
    // /health 실패가 확인된 뒤에만 호출한다. 종료 대상은 local-engine.exe 이름으로 한정한다.
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};

    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(std::io::Error::last_os_error().into());
        }

        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut has_entry = Process32FirstW(snapshot, &mut entry) != 0;
        while has_entry {
            let exe_len = entry
                .szExeFile
                .iter()
                .position(|c| *c == 0)
                .unwrap_or(entry.szExeFile.len());
            let exe_name = String::from_utf16_lossy(&entry.szExeFile[..exe_len]);
            if exe_name.eq_ignore_ascii_case("local-engine.exe") {
                let process = OpenProcess(PROCESS_TERMINATE, 0, entry.th32ProcessID);
                let mut terminated = false;
                if !process.is_null() {
                    terminated = TerminateProcess(process, 1) != 0;
                    CloseHandle(process);
                }
                if !terminated {
                    let _ = std::process::Command::new("taskkill")
                        .args(["/F", "/T", "/PID", &entry.th32ProcessID.to_string()])
                        .status();
                }
            }
            has_entry = Process32NextW(snapshot, &mut entry) != 0;
        }
        CloseHandle(snapshot);
    }
    Ok(())
}

#[cfg(not(windows))]
fn kill_unhealthy_local_engine_processes() -> Result<(), Box<dyn std::error::Error>> {
    Ok(())
}

fn spawn_local_engine(
    app: &tauri::AppHandle,
    profile: &RuntimeProfile,
    repair_unhealthy: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let state = app.state::<SidecarChildren>();
    let log_path = sidecar_log_path(profile, "local-engine.log");
    append_local_engine_log(&log_path, "=== local-engine startup requested ===");
    if health_matches_profile_at(
        LOCAL_ENGINE_HOST,
        profile.engine_port,
        "local_engine",
        profile,
        LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT,
    ) {
        append_local_engine_log(
            &log_path,
            &format!(
                "local-engine already healthy on 127.0.0.1:{}",
                profile.engine_port
            ),
        );
        return Ok(());
    }

    if profile.qa
        && is_tcp_port_open(
            LOCAL_ENGINE_HOST,
            profile.engine_port,
            LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT,
        )
    {
        return Err(format!(
            "QA local-engine 포트 {}가 다른 프로세스에 점유되었거나 instance identity가 불일치합니다. 기존 engine을 종료하거나 재사용하지 않습니다",
            profile.engine_port
        )
        .into());
    }

    if profile.qa {
        if state
            .local_engine
            .lock()
            .expect("local-engine child lock")
            .is_some()
        {
            return Err(
                "QA local-engine child가 실행 중이지만 health identity가 일치하지 않습니다".into(),
            );
        }
    } else if let Some(child) = state
        .local_engine
        .lock()
        .expect("local-engine child lock")
        .take()
    {
        let _ = child.kill();
    }

    if repair_unhealthy && !profile.qa {
        kill_unhealthy_local_engine_processes()?;
        if !wait_for_local_engine_port_released(
            profile.engine_port,
            LOCAL_ENGINE_PORT_RELEASE_TIMEOUT,
        ) {
            return Err(format!(
                "local-engine 서버를 시작할 수 없습니다: 127.0.0.1:{} 포트가 이미 사용 중입니다. 로그: {}",
                profile.engine_port,
                log_path.to_string_lossy()
            )
            .into());
        }
    }

    let mut command = app
        .shell()
        .sidecar("local-engine")?
        .env(
            "CUSTOMER_DB_PATH",
            profile.data_dir.join("customers.sqlite3"),
        )
        .env("RAG_DB_PATH", profile.data_dir.join("rag-index.sqlite3"))
        .env("WHISPER_MODEL_DIR", &profile.model_dir)
        .env("CUSTOMER_DB_KEYFILE", profile.customer_key_path())
        .env("ENGINE_PORT", profile.engine_port.to_string())
        // WebView 의 /api-secret fetch 가 Origin/TLS/CORS 계층에서 막혀도 Tauri 명령이 같은 파일을 읽어 로컬 API 인증을 복구할 수 있게 고정 경로를 넘긴다.
        .env("INSURANCE_AI_API_SECRET_FILE", profile.engine_secret_path())
        // whisper 모델(1.5GB+)은 부팅 시 받지 않는다 — 첫 STT 사용 시 자체 UI 로 다운로드
        .env("WHISPER_PREWARM", "0")
        // 앱이 죽으면 이 락이 풀리고 sidecar 가 자체 종료 (고아 방지)
        .env("APP_LOCK_FILE", profile.lock_path());
    if profile.qa {
        command = command
            .env("CUSTOMER_DB_KEYFILE_ONLY", "1")
            .env("INSURANCE_AI_QA_MODE", "1")
            .env(
                "INSURANCE_AI_INSTANCE_ID",
                profile.instance_id.as_deref().unwrap_or_default(),
            )
            .env(
                "INSURANCE_AI_PROFILE_FINGERPRINT",
                profile.profile_fingerprint.as_deref().unwrap_or_default(),
            )
            .env(
                "INSURANCE_AI_TLS_CERT_FILE",
                profile
                    .qa_tls_cert_path
                    .as_ref()
                    .expect("validated QA TLS cert"),
            )
            .env(
                "INSURANCE_AI_TLS_KEY_FILE",
                profile
                    .qa_tls_key_path
                    .as_ref()
                    .expect("validated QA TLS key"),
            );
    }
    let (mut engine_events, engine_child) = command.spawn().map_err(|e| {
        append_local_engine_log(
            &log_path,
            &format!(
                "local-engine 실행 실패: {e}. packaged sidecar 또는 Python 런타임을 확인하세요."
            ),
        );
        format!(
            "local-engine 서버를 시작할 수 없습니다: 실행 실패({e}). 로그: {}",
            log_path.to_string_lossy()
        )
    })?;
    #[cfg(windows)]
    if let Some(sidecar_job) = app.try_state::<SidecarJob>() {
        sidecar_job.assign(engine_child.pid())?;
    }
    let engine_pid = engine_child.pid();
    *state.local_engine.lock().expect("local-engine child lock") = Some(engine_child);

    append_local_engine_log(
        &log_path,
        &format!("local-engine process spawned pid={engine_pid}"),
    );
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = engine_events.recv().await {
            match event {
                CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                    let line = String::from_utf8_lossy(&l).trim_end().to_owned();
                    eprintln!("[local-engine] {}", line);
                    append_local_engine_log(&log_path, &line);
                }
                CommandEvent::Terminated(p) => {
                    let line = format!(
                        "local-engine 종료됨 code={:?} signal={:?}",
                        p.code, p.signal
                    );
                    eprintln!("[local-engine] {line}");
                    append_local_engine_log(&log_path, &line);
                    if let Some(state) = app_handle.try_state::<SidecarChildren>() {
                        let _ = state
                            .local_engine
                            .lock()
                            .expect("local-engine child lock")
                            .take();
                    }
                }
                CommandEvent::Error(e) => {
                    eprintln!("[local-engine] 오류: {e}");
                    append_local_engine_log(&log_path, &format!("local-engine 이벤트 오류: {e}"));
                }
                _ => {}
            }
        }
    });
    Ok(())
}

#[tauri::command]
fn start_local_engine(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<String, String> {
    spawn_local_engine(&app, &profile, true)
        .map_err(|e| format!("local-engine 서버를 시작할 수 없습니다. {e}"))?;
    Ok(sidecar_log_path(&profile, "local-engine.log")
        .to_string_lossy()
        .into_owned())
}

#[tauri::command]
fn ensure_local_engine(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<(), String> {
    spawn_local_engine(&app, &profile, true).map_err(|e| format!("local-engine 복구 실패: {e}"))?;
    if wait_for_local_engine_health(&profile) {
        Ok(())
    } else {
        let log_path = sidecar_log_path(&profile, "local-engine.log");
        Err(format!(
            "local-engine 서버를 시작할 수 없습니다. /health 확인을 10회 재시도했지만 실패했습니다. 로그: {}",
            log_path.to_string_lossy()
        ))
    }
}

#[tauri::command]
fn local_engine_api_secret(profile: tauri::State<'_, RuntimeProfile>) -> Result<String, String> {
    let value = fs::read_to_string(profile.engine_secret_path())
        .map_err(|e| format!("local-engine 인증 파일을 읽을 수 없습니다: {e}"))?
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err("local-engine 인증 파일이 비어 있습니다".to_owned());
    }
    Ok(value)
}

#[tauri::command]
fn runtime_profile(profile: tauri::State<'_, RuntimeProfile>) -> RuntimeProfileInfo {
    profile.info()
}

async fn bound_loopback_request(
    request: LocalEngineRequest,
    profile: &RuntimeProfile,
    port: u16,
) -> Result<LocalEngineResponse, String> {
    let method = request
        .method
        .parse::<reqwest::Method>()
        .map_err(|e| format!("local-engine 요청 method 오류: {e}"))?;
    let path = if request.path.starts_with('/') {
        request.path
    } else {
        format!("/{}", request.path)
    };
    if path.contains("..") || path.starts_with("//") {
        return Err("local-engine 요청 path가 올바르지 않습니다".to_owned());
    }
    let scheme = if profile.qa { "https" } else { "http" };
    let url = format!("{scheme}://{}:{}{}", LOCAL_ENGINE_HOST, port, path);
    let mut client_builder = reqwest::Client::builder().timeout(Duration::from_secs(180));
    if profile.qa {
        client_builder = client_builder
            .redirect(reqwest::redirect::Policy::none())
            .tls_built_in_root_certs(false)
            .add_root_certificate(qa_pinned_certificate(profile)?);
    }
    let client = client_builder
        .build()
        .map_err(|e| format!("local-engine client 생성 실패: {e}"))?;
    let mut builder = client.request(method, url);
    for (name, value) in request.headers {
        let lower = name.to_ascii_lowercase();
        if lower == "host"
            || lower == "origin"
            || lower == "referer"
            || lower == "content-length"
            || lower == QA_INSTANCE_HEADER.to_ascii_lowercase()
            || lower == QA_BINDING_HEADER.to_ascii_lowercase()
        {
            continue;
        }
        builder = builder.header(name, value);
    }
    if profile.qa {
        let instance_id = profile
            .instance_id
            .as_ref()
            .ok_or_else(|| "QA instance identity가 없습니다".to_owned())?;
        let binding = profile
            .profile_fingerprint
            .as_ref()
            .ok_or_else(|| "QA profile binding이 없습니다".to_owned())?;
        builder = builder
            .header(QA_INSTANCE_HEADER, instance_id)
            .header(QA_BINDING_HEADER, binding);
    }
    if let Some(body) = request.body {
        builder = match body {
            LocalEngineRequestBody::Text { text } => builder.body(text),
            LocalEngineRequestBody::Bytes { bytes } => builder.body(bytes),
            LocalEngineRequestBody::FormData { fields } => {
                let mut form = reqwest::multipart::Form::new();
                for field in fields {
                    if let Some(bytes) = field.bytes {
                        let mut part = reqwest::multipart::Part::bytes(bytes);
                        if let Some(file_name) = field.file_name {
                            part = part.file_name(file_name);
                        }
                        if let Some(content_type) =
                            field.content_type.filter(|value| !value.is_empty())
                        {
                            part = part.mime_str(&content_type).map_err(|e| {
                                format!("local-engine multipart content-type 오류: {e}")
                            })?;
                        }
                        form = form.part(field.name, part);
                    } else {
                        form = form.text(field.name, field.value.unwrap_or_default());
                    }
                }
                builder.multipart(form)
            }
        };
    }
    let response = builder
        .send()
        .await
        .map_err(|e| format!("local-engine native request 실패: {e}"))?;
    let status = response.status().as_u16();
    let headers = response
        .headers()
        .iter()
        .filter_map(|(name, value)| {
            let lower = name.as_str().to_ascii_lowercase();
            if lower == "transfer-encoding"
                || lower == "content-encoding"
                || lower == "content-length"
            {
                return None;
            }
            value
                .to_str()
                .ok()
                .map(|value| (name.to_string(), value.to_owned()))
        })
        .collect();
    let body = response
        .bytes()
        .await
        .map(|bytes| bytes.to_vec())
        .map_err(|e| format!("local-engine 응답 읽기 실패: {e}"))?;
    Ok(LocalEngineResponse {
        status,
        body,
        headers,
    })
}

#[tauri::command]
async fn local_engine_request(
    request: LocalEngineRequest,
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<LocalEngineResponse, String> {
    bound_loopback_request(request, &profile, profile.engine_port).await
}

#[tauri::command]
async fn control_server_request(
    request: LocalEngineRequest,
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<LocalEngineResponse, String> {
    bound_loopback_request(request, &profile, profile.control_port).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::process::Command;
    use std::thread;
    use std::time::{SystemTime, UNIX_EPOCH};

    const TEST_QA_CERT_PEM: &str = "-----BEGIN CERTIFICATE-----\nMIIBzjCCAXWgAwIBAgIULbfHRS6DZtU0jR5RkVkucYNWsIMwCgYIKoZIzj0EAwIw\nPzE9MDsGA1UEAww0SW5zdXJhbmNlIEFJIFFBIDhmNjMwZjk0LTIwNzktNDk3My1i\nZDdjLTJhMzllMDc4NGY2YzAeFw0yNjEwMjAxNDMyMjJaFw0yNjEwMjExNDM3MjJa\nMD8xPTA7BgNVBAMMNEluc3VyYW5jZSBBSSBRQSA4ZjYzMGY5NC0yMDc5LTQ5NzMt\nYmQ3Yy0yYTM5ZTA3ODRmNmMwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQE35I7\nmdOPpR+TOS+UlAv4RoG9hBRdijDHMNKNu7dRdouicxSJmRU6zoaN1eHR3H3YtcAt\n9G7EInuUmhTW/7BWo08wTTASBgNVHREBAf8ECDAGhwR/AAABMBIGA1UdEwEB/wQI\nMAYBAf8CAQAwEwYDVR0lBAwwCgYIKwYBBQUHAwEwDgYDVR0PAQH/BAQDAgKMMAoG\nCCqGSM49BAMCA0cAMEQCID3HVbdRi3qMOlbn9MQFyW+ZrJa9ldhJ8N5R/Wc6jg0o\nAiA12NFya7PP8pNeg5A0/UgmpxO45/R3RdEKiQd4cgqsUg==\n-----END CERTIFICATE-----\n";

    fn localhost_listener() -> (TcpListener, u16) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind test listener");
        let port = listener.local_addr().expect("listener addr").port();
        (listener, port)
    }

    fn unique_test_dir(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "insurance-ai-{label}-{}-{nonce}",
            std::process::id()
        ))
    }

    fn free_port() -> u16 {
        let (listener, port) = localhost_listener();
        drop(listener);
        port
    }

    fn write_marker(root: &Path, instance_id: &str, fingerprint: &str) {
        fs::create_dir_all(root).expect("create profile root");
        let config = root.join("config");
        fs::create_dir_all(&config).expect("create config");
        fs::write(config.join("qa-loopback-cert.pem"), TEST_QA_CERT_PEM).expect("write test cert");
        fs::write(config.join("qa-loopback-key.pem"), b"test-only-placeholder")
            .expect("write test key placeholder");
        fs::write(
            root.join(QA_MARKER_NAME),
            format!(
                "{{\"schema\":2,\"instance_id\":\"{instance_id}\",\"profile_fingerprint\":\"{fingerprint}\",\"transport\":\"pinned-tls-v1\"}}"
            ),
        )
        .expect("write marker");
    }

    fn qa_args(
        root: PathBuf,
        instance_id: &str,
        engine_port: u16,
        control_port: u16,
    ) -> QaLaunchArgs {
        QaLaunchArgs {
            root: Some(root),
            instance_id: Some(instance_id.to_owned()),
            engine_port: Some(engine_port),
            control_port: Some(control_port),
            saw_qa_arg: true,
        }
    }

    fn qa_health_profile(port: u16) -> RuntimeProfile {
        let mut profile =
            production_runtime_profile(PathBuf::from("config"), PathBuf::from("data"));
        profile.qa = true;
        profile.engine_port = port;
        profile.instance_id = Some("12345678-1234-1234-1234-123456789abc".to_owned());
        profile.profile_fingerprint = Some("a".repeat(64));
        profile.qa_tls_cert_pem = Some(TEST_QA_CERT_PEM.as_bytes().to_vec());
        profile
    }

    #[test]
    fn qa_launch_is_opt_in_and_partial_args_fail_closed() {
        let normal = parse_qa_launch_args([OsString::from("app.exe")]).expect("parse default");
        assert!(normal.is_none());
        let partial = parse_qa_launch_args([
            OsString::from("app.exe"),
            OsString::from("--qa-profile-dir"),
            OsString::from("C:\\qa"),
        ]);
        assert!(partial.is_err());
    }

    #[test]
    fn qa_profile_rejects_relative_unc_or_missing_root() {
        for root in [
            PathBuf::from("relative-qa-root"),
            PathBuf::from(r"\\server\share\qa-root"),
        ] {
            let result = qa_runtime_profile(
                qa_args(
                    root,
                    "12345678-1234-1234-1234-123456789abc",
                    free_port(),
                    free_port(),
                ),
                Path::new("production-data"),
                Path::new("production-config"),
            );
            assert!(result.is_err());
        }
    }

    #[test]
    fn production_profile_preserves_default_paths_and_ports() {
        let profile =
            production_runtime_profile(PathBuf::from("prod-config"), PathBuf::from("prod-data"));
        assert!(!profile.qa);
        assert_eq!(profile.config_dir, PathBuf::from("prod-config"));
        assert_eq!(profile.data_dir, PathBuf::from("prod-data"));
        assert_eq!(profile.engine_port, 8420);
        assert_eq!(profile.control_port, 8790);
        assert_eq!(
            profile.lock_path(),
            PathBuf::from("prod-data").join("app.lock")
        );
        assert_eq!(
            profile.engine_secret_path(),
            PathBuf::from("prod-data").join("local-engine-api.secret")
        );
    }

    #[test]
    fn qa_profile_rejects_marker_mismatch_and_never_falls_back() {
        let root = unique_test_dir("marker-mismatch");
        let production_data = unique_test_dir("production-data");
        let production_config = unique_test_dir("production-config");
        let expected = "12345678-1234-1234-1234-123456789abc";
        write_marker(
            &root,
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            &"b".repeat(64),
        );
        let result = qa_runtime_profile(
            qa_args(root.clone(), expected, free_port(), free_port()),
            &production_data,
            &production_config,
        );
        assert!(result.is_err());
        assert!(!production_runtime_profile(production_config, production_data).qa);
        fs::remove_dir_all(root).expect("cleanup root");
    }

    #[test]
    fn qa_profile_keeps_every_runtime_path_under_validated_root() {
        let root = unique_test_dir("valid-root");
        let production_data = unique_test_dir("production-data");
        let production_config = unique_test_dir("production-config");
        let instance = "12345678-1234-1234-1234-123456789abc";
        write_marker(&root, instance, &"c".repeat(64));
        let profile = qa_runtime_profile(
            qa_args(root.clone(), instance, free_port(), free_port()),
            &production_data,
            &production_config,
        )
        .expect("valid QA profile");
        let canonical = root.canonicalize().expect("canonical root");
        let paths = [
            profile.data_dir.clone(),
            profile.data_dir.join("customers.sqlite3"),
            profile.data_dir.join("rag-index.sqlite3"),
            profile.data_dir.join("control.sqlite3"),
            profile.data_dir.join("backups"),
            profile.config_dir.clone(),
            profile.webview_dir.clone().expect("webview"),
            profile.log_dir.clone(),
            profile.model_dir.clone(),
            profile.lock_path(),
            profile.engine_secret_path(),
            profile.customer_key_path(),
            profile.control_secrets_path(),
        ];
        for path in paths {
            assert!(
                path.starts_with(&canonical),
                "escaped path: {}",
                path.display()
            );
        }
        fs::remove_dir_all(root).expect("cleanup root");
    }

    #[test]
    fn qa_profile_rejects_production_path_and_occupied_port() {
        let production = unique_test_dir("production-overlap");
        let config = unique_test_dir("config");
        let instance = "12345678-1234-1234-1234-123456789abc";
        write_marker(&production, instance, &"d".repeat(64));
        let overlap = qa_runtime_profile(
            qa_args(production.clone(), instance, free_port(), free_port()),
            &production,
            &config,
        );
        assert!(overlap.is_err());

        let (listener, occupied) = localhost_listener();
        assert!(ensure_ports_available(&[occupied]).is_err());
        drop(listener);
        fs::remove_dir_all(production).expect("cleanup production");
    }

    #[cfg(windows)]
    #[test]
    fn qa_profile_rejects_default_path_junction_alias() {
        let production = unique_test_dir("junction-production");
        let alias = unique_test_dir("junction-alias");
        let config = unique_test_dir("junction-config");
        fs::create_dir_all(&production).expect("create production");
        let status = Command::new("cmd")
            .args(["/c", "mklink", "/J"])
            .arg(&alias)
            .arg(&production)
            .status()
            .expect("run mklink");
        assert!(status.success());
        let result = qa_runtime_profile(
            qa_args(
                alias.clone(),
                "12345678-1234-1234-1234-123456789abc",
                free_port(),
                free_port(),
            ),
            &production,
            &config,
        );
        assert!(result.is_err());
        fs::remove_dir(&alias).expect("remove junction");
        fs::remove_dir_all(production).expect("remove production");
    }

    #[test]
    fn qa_health_accepts_only_matching_instance_identity() {
        let profile = qa_health_profile(free_port());
        let body = serde_json::json!({
            "local_engine": "ok",
            "instance_id": "12345678-1234-1234-1234-123456789abc",
            "profile_fingerprint": "a".repeat(64),
        });
        assert!(health_body_matches_profile(&body, "local_engine", &profile));
    }

    #[test]
    fn qa_health_rejects_external_instance_mismatch() {
        let profile = qa_health_profile(free_port());
        let body = serde_json::json!({
            "local_engine": "ok",
            "instance_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "profile_fingerprint": "f".repeat(64),
        });
        assert!(!health_body_matches_profile(
            &body,
            "local_engine",
            &profile
        ));
    }

    #[test]
    fn qa_pinned_tls_authenticates_peer_before_http_credentials_or_body() {
        let (listener, port) = localhost_listener();
        let capture = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept TLS client");
            let mut bytes = [0_u8; 8192];
            let count = stream.read(&mut bytes).expect("read TLS client hello");
            bytes[..count].to_vec()
        });
        let profile = qa_health_profile(port);
        let header_canary = "qa-api-secret-canary-never-send";
        let bearer_canary = "qa-bearer-canary-never-send";
        let body_canary = "qa-body-canary-never-send";
        let request = LocalEngineRequest {
            method: "POST".to_owned(),
            path: "/customers".to_owned(),
            headers: vec![
                ("X-Insurance-AI-Secret".to_owned(), header_canary.to_owned()),
                (
                    "Authorization".to_owned(),
                    format!("Bearer {bearer_canary}"),
                ),
            ],
            body: Some(LocalEngineRequestBody::Text {
                text: body_canary.to_owned(),
            }),
        };
        let runtime = tokio::runtime::Runtime::new().expect("test runtime");
        let result = runtime.block_on(bound_loopback_request(request, &profile, port));
        assert!(result.is_err(), "foreign listener must fail pinned TLS");
        let captured = capture.join().expect("join fake listener");
        assert!(
            !captured.is_empty(),
            "TLS ClientHello must reach the fake listener"
        );
        for forbidden in [
            header_canary,
            bearer_canary,
            body_canary,
            profile.instance_id.as_deref().expect("instance"),
            profile.profile_fingerprint.as_deref().expect("binding"),
        ] {
            assert!(
                !captured
                    .windows(forbidden.len())
                    .any(|window| window == forbidden.as_bytes()),
                "sensitive HTTP material reached the unauthenticated listener"
            );
        }
    }

    #[test]
    fn health_probe_times_out_when_port_accepts_but_never_responds() {
        let (listener, port) = localhost_listener();
        let handle = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0_u8; 128];
                let _ = stream.read(&mut buf);
                thread::sleep(Duration::from_secs(2));
            }
        });

        let started = Instant::now();
        assert!(!local_engine_is_healthy_at(
            "127.0.0.1",
            port,
            Duration::from_millis(250)
        ));
        assert!(started.elapsed() < Duration::from_secs(2));
        let _ = handle.join();
    }

    #[test]
    fn health_probe_accepts_local_engine_ok_response() {
        let (listener, port) = localhost_listener();
        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept health probe");
            let mut buf = [0_u8; 128];
            let _ = stream.read(&mut buf);
            let body = "{\"local_engine\":\"ok\"}";
            let mut response = Vec::new();
            response.extend_from_slice(b"HTTP/1.1 200 OK");
            response.extend_from_slice(&[13, 10]);
            response.extend_from_slice(b"Content-Length: ");
            response.extend_from_slice(body.len().to_string().as_bytes());
            response.extend_from_slice(&[13, 10]);
            response.extend_from_slice(b"Connection: close");
            response.extend_from_slice(&[13, 10, 13, 10]);
            response.extend_from_slice(body.as_bytes());
            stream.write_all(&response).expect("write response");
        });

        assert!(local_engine_is_healthy_at(
            "127.0.0.1",
            port,
            Duration::from_secs(1)
        ));
        let _ = handle.join();
    }

    #[test]
    fn wait_for_tcp_port_released_waits_until_listener_drops() {
        let (listener, port) = localhost_listener();
        let handle = thread::spawn(move || {
            thread::sleep(Duration::from_millis(300));
            drop(listener);
        });

        assert!(wait_for_tcp_port_released(
            "127.0.0.1",
            port,
            Duration::from_secs(2)
        ));
        let _ = handle.join();
    }
}

fn terminate_managed_sidecars(app_handle: &tauri::AppHandle) {
    #[cfg(windows)]
    if let Some(job) = app_handle.try_state::<SidecarJob>() {
        job.terminate_all();
    }
    if let Some(state) = app_handle.try_state::<SidecarChildren>() {
        if let Some(child) = state
            .control_server
            .lock()
            .expect("control-server child lock")
            .take()
        {
            let _ = child.kill();
        }
        if let Some(child) = state
            .local_engine
            .lock()
            .expect("local-engine child lock")
            .take()
        {
            let _ = child.kill();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            greet,
            ollama::check_ollama_installed,
            ollama::install_ollama,
            ollama::check_models_installed,
            ollama::download_model,
            start_local_engine,
            ensure_local_engine,
            local_engine_log_path,
            local_engine_api_secret,
            local_engine_request,
            control_server_request,
            runtime_profile,
        ])
        .setup(|app| {
            let production_config_dir = app.path().app_config_dir()?;
            let production_data_dir = app.path().app_data_dir()?;
            let profile =
                match parse_qa_launch_args(std::env::args_os()).map_err(std::io::Error::other)? {
                    Some(args) => {
                        qa_runtime_profile(args, &production_data_dir, &production_config_dir)
                            .map_err(std::io::Error::other)?
                    }
                    None => production_runtime_profile(production_config_dir, production_data_dir),
                };
            for dir in [
                &profile.data_dir,
                &profile.config_dir,
                &profile.log_dir,
                &profile.model_dir,
            ] {
                fs::create_dir_all(dir)?;
            }
            if let Some(webview_dir) = &profile.webview_dir {
                fs::create_dir_all(webview_dir)?;
            }
            if let Some(parent) = profile.lock_path().parent() {
                fs::create_dir_all(parent)?;
            }

            // 사이드카 생존 신호용 잠금 파일. 이 File 을 앱 상태로 보관해 fd 를 열어둔다.
            let lock_file = OpenOptions::new()
                .create(true)
                .write(true)
                .open(profile.lock_path())?;
            lock_file.lock_exclusive()?;
            app.manage(AppLock(lock_file));
            app.manage(profile.clone());

            #[cfg(windows)]
            let sidecar_job = SidecarJob::new()?;

            let (jwt_secret, admin_token) =
                load_or_create_secrets(&profile.control_secrets_path())?;
            let mut control_command = app
                .shell()
                .sidecar("control-server")?
                .env("CONTROL_JWT_SECRET", jwt_secret)
                .env("CONTROL_ADMIN_TOKEN", admin_token)
                .env("CONTROL_LICENSE_SECRET", "dev-license-secret-change-me")
                .env("CONTROL_LOCAL_RECOVERY", "1")
                .env("CONTROL_DB_PATH", profile.data_dir.join("control.sqlite3"))
                .env("CONTROL_PORT", profile.control_port.to_string())
                // 앱이 죽으면 이 락이 풀리고 sidecar 가 자체 종료 (고아 방지)
                .env("APP_LOCK_FILE", profile.lock_path());
            if profile.qa {
                control_command = control_command
                    .env("INSURANCE_AI_QA_MODE", "1")
                    .env(
                        "INSURANCE_AI_INSTANCE_ID",
                        profile.instance_id.as_deref().unwrap_or_default(),
                    )
                    .env(
                        "INSURANCE_AI_PROFILE_FINGERPRINT",
                        profile.profile_fingerprint.as_deref().unwrap_or_default(),
                    )
                    .env(
                        "INSURANCE_AI_TLS_CERT_FILE",
                        profile
                            .qa_tls_cert_path
                            .as_ref()
                            .expect("validated QA TLS cert"),
                    )
                    .env(
                        "INSURANCE_AI_TLS_KEY_FILE",
                        profile
                            .qa_tls_key_path
                            .as_ref()
                            .expect("validated QA TLS key"),
                    );
            }
            let (mut events, child) = control_command.spawn()?;
            #[cfg(windows)]
            sidecar_job.assign(child.pid())?;
            #[cfg(windows)]
            app.manage(sidecar_job);
            let control_log_path = sidecar_log_path(&profile, "control-server.log");
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    let line = match event {
                        CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                            format!("{}", String::from_utf8_lossy(&l).trim_end())
                        }
                        CommandEvent::Terminated(p) => format!(
                            "control-server 종료됨 code={:?} signal={:?}",
                            p.code, p.signal
                        ),
                        CommandEvent::Error(e) => format!("control-server 오류: {e}"),
                        _ => continue,
                    };
                    eprintln!("[control-server] {line}");
                    append_local_engine_log(&control_log_path, &line);
                }
            });
            app.manage(SidecarChildren {
                control_server: Mutex::new(Some(child)),
                local_engine: Mutex::new(None),
            });
            if let Err(error) = spawn_local_engine(app.handle(), &profile, true) {
                let log_path = sidecar_log_path(&profile, "local-engine.log");
                append_local_engine_log(&log_path, &format!("startup spawn failed: {error}"));
                eprintln!("[local-engine] startup spawn failed: {error}");
                if profile.qa {
                    return Err(std::io::Error::other(error.to_string()).into());
                }
            }
            if profile.qa {
                if !wait_for_local_engine_health(&profile) {
                    return Err(std::io::Error::other(
                        "QA local-engine health identity 검증에 실패했습니다",
                    )
                    .into());
                }
                if !wait_for_profile_service_health(
                    &profile,
                    "control_server",
                    profile.control_port,
                ) {
                    return Err(std::io::Error::other(
                        "QA control-server health identity 검증에 실패했습니다",
                    )
                    .into());
                }
            }

            let builder = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("모아둠")
            .inner_size(1000.0, 720.0);
            #[cfg(windows)]
            let builder = builder.disable_drag_drop_handler();
            let builder = if let Some(webview_dir) = &profile.webview_dir {
                builder.data_directory(webview_dir.clone())
            } else {
                builder
            };
            builder.build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|app_handle, event| {
        let main_close_requested = matches!(
            &event,
            RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::CloseRequested { .. },
                ..
            } if label == "main"
        );
        if main_close_requested {
            terminate_managed_sidecars(app_handle);
            app_handle.exit(0);
            return;
        }
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            terminate_managed_sidecars(app_handle);
        }
    });
}
