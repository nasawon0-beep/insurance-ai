mod ollama;

use fs2::FileExt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
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
const LOCAL_ENGINE_STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

fn local_engine_is_healthy(timeout: Duration) -> bool {
    local_engine_is_healthy_at(LOCAL_ENGINE_HOST, LOCAL_ENGINE_PORT, timeout)
}

fn local_engine_is_healthy_at(host: &str, port: u16, timeout: Duration) -> bool {
    let Ok(mut addrs) = (host, port).to_socket_addrs() else {
        return false;
    };
    let Some(addr) = addrs.next() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    const HEALTH_REQUEST: &[u8] = &[
        71, 69, 84, 32, 47, 104, 101, 97, 108, 116, 104, 32, 72, 84, 84, 80, 47, 49, 46, 49, 13,
        10, 72, 111, 115, 116, 58, 32, 49, 50, 55, 46, 48, 46, 48, 46, 49, 58, 56, 52, 50, 48, 13,
        10, 67, 111, 110, 110, 101, 99, 116, 105, 111, 110, 58, 32, 99, 108, 111, 115, 101, 13, 10,
        13, 10,
    ];
    if stream.write_all(HEALTH_REQUEST).is_err() {
        return false;
    }
    let deadline = Instant::now() + timeout;
    let mut response = Vec::new();
    let mut buf = [0_u8; 512];
    while Instant::now() < deadline {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                response.extend_from_slice(&buf[..n]);
                let text = String::from_utf8_lossy(&response);
                if (text.starts_with("HTTP/1.1 200") || text.starts_with("HTTP/1.0 200"))
                    && text.contains("\"local_engine\"")
                    && text.contains("\"ok\"")
                {
                    return true;
                }
            }
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                break;
            }
            Err(_) => return false,
        }
    }
    let response = String::from_utf8_lossy(&response);
    (response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200"))
        && response.contains("\"local_engine\"")
        && response.contains("\"ok\"")
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

fn wait_for_local_engine_port_released(total_timeout: Duration) -> bool {
    wait_for_tcp_port_released(LOCAL_ENGINE_HOST, LOCAL_ENGINE_PORT, total_timeout)
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

fn wait_for_local_engine_health(total_timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < total_timeout {
        if local_engine_is_healthy(LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
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
    config_dir: &Path,
    data_dir: &Path,
    lock_path_str: &str,
    repair_unhealthy: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let state = app.state::<SidecarChildren>();
    if local_engine_is_healthy(LOCAL_ENGINE_HEALTH_PROBE_TIMEOUT) {
        return Ok(());
    }

    if let Some(child) = state
        .local_engine
        .lock()
        .expect("local-engine child lock")
        .take()
    {
        let _ = child.kill();
    }

    if repair_unhealthy {
        kill_unhealthy_local_engine_processes()?;
        if !wait_for_local_engine_port_released(LOCAL_ENGINE_PORT_RELEASE_TIMEOUT) {
            return Err(
                "stale local-engine.exe 종료 후에도 127.0.0.1:8420 포트가 해제되지 않았습니다"
                    .into(),
            );
        }
    }

    let (mut engine_events, engine_child) = app
        .shell()
        .sidecar("local-engine")?
        .env("CUSTOMER_DB_PATH", data_dir.join("customers.sqlite3"))
        .env("RAG_DB_PATH", data_dir.join("rag-index.sqlite3"))
        .env("WHISPER_MODEL_DIR", data_dir.join("whisper-models"))
        .env("CUSTOMER_DB_KEYFILE", config_dir.join("customer-db.key"))
        // WebView 의 /api-secret fetch 가 Origin/TLS/CORS 계층에서 막혀도 Tauri 명령이 같은 파일을 읽어 로컬 API 인증을 복구할 수 있게 고정 경로를 넘긴다.
        .env(
            "INSURANCE_AI_API_SECRET_FILE",
            data_dir.join("local-engine-api.secret"),
        )
        // whisper 모델(1.5GB+)은 부팅 시 받지 않는다 — 첫 STT 사용 시 자체 UI 로 다운로드
        .env("WHISPER_PREWARM", "0")
        // 앱이 죽으면 이 락이 풀리고 sidecar 가 자체 종료 (고아 방지)
        .env("APP_LOCK_FILE", lock_path_str)
        .spawn()?;
    #[cfg(windows)]
    if let Some(sidecar_job) = app.try_state::<SidecarJob>() {
        sidecar_job.assign(engine_child.pid())?;
    }
    *state.local_engine.lock().expect("local-engine child lock") = Some(engine_child);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = engine_events.recv().await {
            match event {
                CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                    eprintln!("[local-engine] {}", String::from_utf8_lossy(&l).trim_end())
                }
                CommandEvent::Terminated(p) => {
                    eprintln!(
                        "[local-engine] 종료됨 code={:?} signal={:?}",
                        p.code, p.signal
                    );
                    if let Some(state) = app_handle.try_state::<SidecarChildren>() {
                        let _ = state
                            .local_engine
                            .lock()
                            .expect("local-engine child lock")
                            .take();
                    }
                }
                CommandEvent::Error(e) => eprintln!("[local-engine] 오류: {e}"),
                _ => {}
            }
        }
    });
    Ok(())
}

#[tauri::command]
fn ensure_local_engine(app: tauri::AppHandle) -> Result<(), String> {
    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("설정 폴더 확인 실패: {e}"))?;
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("데이터 폴더 확인 실패: {e}"))?;
    fs::create_dir_all(&data_dir).map_err(|e| format!("데이터 폴더 생성 실패: {e}"))?;
    let lock_path_str = data_dir.join("app.lock").to_string_lossy().into_owned();
    spawn_local_engine(&app, &config_dir, &data_dir, &lock_path_str, true)
        .map_err(|e| format!("local-engine 복구 실패: {e}"))?;
    if wait_for_local_engine_health(LOCAL_ENGINE_STARTUP_TIMEOUT) {
        Ok(())
    } else {
        Err(
            "local-engine 재시작 후 60초 동안 /health 응답이 없습니다. 실행 중인 local-engine.exe를 종료한 뒤 앱을 다시 시작해 주세요."
                .to_owned(),
        )
    }
}

#[tauri::command]
fn local_engine_api_secret(app: tauri::AppHandle) -> Result<String, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("데이터 폴더 확인 실패: {e}"))?;
    let path = data_dir.join("local-engine-api.secret");
    let value = fs::read_to_string(path)
        .map_err(|e| format!("local-engine 인증 파일을 읽을 수 없습니다: {e}"))?
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err("local-engine 인증 파일이 비어 있습니다".to_owned());
    }
    Ok(value)
}

#[tauri::command]
async fn local_engine_request(request: LocalEngineRequest) -> Result<LocalEngineResponse, String> {
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
    let url = format!("http://{}:{}{}", LOCAL_ENGINE_HOST, LOCAL_ENGINE_PORT, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(180))
        .build()
        .map_err(|e| format!("local-engine client 생성 실패: {e}"))?;
    let mut builder = client.request(method, url);
    for (name, value) in request.headers {
        let lower = name.to_ascii_lowercase();
        if lower == "host" || lower == "origin" || lower == "referer" || lower == "content-length" {
            continue;
        }
        builder = builder.header(name, value);
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::thread;

    fn localhost_listener() -> (TcpListener, u16) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind test listener");
        let port = listener.local_addr().expect("listener addr").port();
        (listener, port)
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
            ensure_local_engine,
            local_engine_api_secret,
            local_engine_request,
        ])
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let data_dir = app.path().app_data_dir()?;
            fs::create_dir_all(&data_dir)?;

            // 사이드카 생존 신호용 잠금 파일. 이 File 을 앱 상태로 보관해 fd 를 열어둔다.
            let lock_path = data_dir.join("app.lock");
            let lock_path_str = lock_path.to_string_lossy().into_owned();
            let lock_file = OpenOptions::new()
                .create(true)
                .write(true)
                .open(&lock_path)?;
            lock_file.lock_exclusive()?;
            app.manage(AppLock(lock_file));

            #[cfg(windows)]
            let sidecar_job = SidecarJob::new()?;

            let (jwt_secret, admin_token) =
                load_or_create_secrets(&config_dir.join("control-secrets.env"))?;
            let (mut events, child) = app
                .shell()
                .sidecar("control-server")?
                .env("CONTROL_JWT_SECRET", jwt_secret)
                .env("CONTROL_ADMIN_TOKEN", admin_token)
                .env("CONTROL_LICENSE_SECRET", "dev-license-secret-change-me")
                .env("CONTROL_LOCAL_RECOVERY", "1")
                .env("CONTROL_DB_PATH", data_dir.join("control.sqlite3"))
                // 앱이 죽으면 이 락이 풀리고 sidecar 가 자체 종료 (고아 방지)
                .env("APP_LOCK_FILE", &lock_path_str)
                .spawn()?;
            #[cfg(windows)]
            sidecar_job.assign(child.pid())?;
            #[cfg(windows)]
            app.manage(sidecar_job);
            // sidecar 출력을 앱 stderr 로 흘려 진단 가능하게 하고 조기 종료를 표면화한다.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                            eprintln!(
                                "[control-server] {}",
                                String::from_utf8_lossy(&l).trim_end()
                            )
                        }
                        CommandEvent::Terminated(p) => eprintln!(
                            "[control-server] 종료됨 code={:?} signal={:?}",
                            p.code, p.signal
                        ),
                        CommandEvent::Error(e) => eprintln!("[control-server] 오류: {e}"),
                        _ => {}
                    }
                }
            });
            app.manage(SidecarChildren {
                control_server: Mutex::new(Some(child)),
                local_engine: Mutex::new(None),
            });
            spawn_local_engine(app.handle(), &config_dir, &data_dir, &lock_path_str, true)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
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
    });
}
