use crate::{is_tcp_port_open, RuntimeProfile, SidecarJob};
use mdns_sd::{ServiceDaemon, ServiceInfo};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::net::{IpAddr, Ipv4Addr, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

const MOBILE_SYNC_PORT: u16 = 18443;
const START_TIMEOUT: Duration = Duration::from_secs(60);
const STOP_TIMEOUT: Duration = Duration::from_secs(10);
const DISCOVERY_SERVICE_TYPE: &str = "_moadoom-sync._tcp.local.";

fn is_valid_instance_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|character| character.is_ascii_digit() || (b'a'..=b'f').contains(&character))
}

fn discovery_txt_properties(instance_id: &str) -> HashMap<String, String> {
    HashMap::from([
        ("version".to_owned(), "1".to_owned()),
        ("instance".to_owned(), instance_id.to_owned()),
    ])
}

struct MobileDiscoveryAdvertisement {
    daemon: ServiceDaemon,
    fullname: String,
}

impl MobileDiscoveryAdvertisement {
    fn start(host: &str, instance_id: &str) -> Result<Self, String> {
        let daemon = ServiceDaemon::new()
            .map_err(|error| format!("모바일 PC 발견 광고 시작 실패: {error}"))?;
        let service_name = format!("MoaDoom-{}", &instance_id[..12]);
        let host_name = format!("moadoom-{instance_id}.local.");
        let service = ServiceInfo::new(
            DISCOVERY_SERVICE_TYPE,
            &service_name,
            &host_name,
            host,
            MOBILE_SYNC_PORT,
            discovery_txt_properties(instance_id),
        )
        .map_err(|error| format!("모바일 PC 발견 정보 생성 실패: {error}"))?
        .enable_addr_auto();
        let fullname = service.get_fullname().to_owned();
        daemon
            .register(service)
            .map_err(|error| format!("모바일 PC 발견 광고 등록 실패: {error}"))?;
        Ok(Self { daemon, fullname })
    }

    fn stop(self) {
        let _ = self.daemon.unregister(&self.fullname);
        let _ = self.daemon.shutdown();
    }
}

#[derive(Default)]
struct MobileSyncInner {
    starting: bool,
    child: Option<CommandChild>,
    job: Option<SidecarJob>,
    host: Option<String>,
    fingerprint: Option<String>,
    certificate_expires_at: Option<i64>,
    shutdown_file: Option<PathBuf>,
    last_error: Option<String>,
    discovery: Option<MobileDiscoveryAdvertisement>,
    // 사용자가 켠 모바일 연결이 따라갈 PC 주소. 수동 중지·로그아웃 때만 비운다(네트워크 변경 자동 재시작용).
    follow_host: Option<String>,
    // 사용자 의도(시작/중지/로그아웃)가 바뀔 때마다 증가. 자동 재시작은 시작 시점 세대와 같을 때만 진행·확정한다.
    intent_generation: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum StartIntent {
    User,
    Follow(u64),
}

/// 시작 진입 시 의도 확인. 사용자 시작은 새 세대를 만들고, 자동 재시작은 세대가 그대로이고 follow가 켜져 있을 때만 허용.
fn begin_start_intent(inner: &mut MobileSyncInner, intent: StartIntent) -> Result<u64, String> {
    match intent {
        StartIntent::User => {
            inner.intent_generation = inner.intent_generation.wrapping_add(1);
            Ok(inner.intent_generation)
        }
        StartIntent::Follow(generation)
            if generation == inner.intent_generation && inner.follow_host.is_some() =>
        {
            Ok(generation)
        }
        StartIntent::Follow(_) => Err(FOLLOW_CANCELLED.into()),
    }
}

/// 수동 중지·로그아웃: 진행 중인 자동 재시작이 이 의도를 뒤집지 못하게 세대를 올리고 follow를 끈다.
fn end_user_intent(inner: &mut MobileSyncInner) {
    inner.intent_generation = inner.intent_generation.wrapping_add(1);
    inner.follow_host = None;
}

const FOLLOW_CANCELLED: &str = "사용자가 모바일 연결을 중지해 자동 재시작을 취소했습니다";

const NETWORK_FOLLOW_INTERVAL: Duration = Duration::from_secs(10);

#[derive(Debug, PartialEq)]
enum NetworkFollowAction {
    Keep,
    Restart(String),
}

/// Wi-Fi↔핫스팟 전환·DHCP 변경으로 PC 주소가 바뀌면 같은 신뢰 identity로 서버를 새 주소에서 다시 띄운다.
/// 주소가 잡히지 않는 순간(전환 중)·시작 중·사용자가 끈 상태에서는 아무것도 하지 않는다.
/// 새 주소마다 한 번만 시도한다(실패 시 반복하지 않고 오류를 화면에 남김).
fn network_follow_action(
    follow_host: Option<&str>,
    starting: bool,
    detected: Option<&str>,
) -> NetworkFollowAction {
    match (follow_host, detected) {
        (Some(follow), Some(now)) if !starting && follow != now => {
            NetworkFollowAction::Restart(now.to_owned())
        }
        _ => NetworkFollowAction::Keep,
    }
}

pub(crate) fn spawn_network_follow(app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(NETWORK_FOLLOW_INTERVAL).await;
            let Some(runtime) = app.try_state::<MobileSyncRuntime>() else {
                continue;
            };
            let (follow, starting, generation) = {
                let inner = runtime.0.lock().expect("mobile sync state lock");
                (inner.follow_host.clone(), inner.starting, inner.intent_generation)
            };
            if follow.is_none() {
                continue;
            }
            let detected = detected_private_ipv4().ok();
            let NetworkFollowAction::Restart(next) =
                network_follow_action(follow.as_deref(), starting, detected.as_deref())
            else {
                continue;
            };
            let Some(profile) = app.try_state::<RuntimeProfile>() else {
                continue;
            };
            // 사용자 시작과 자동 재시작을 직렬화한다(중지 명령은 세대값으로 우선).
            let rt: &MobileSyncRuntime = runtime.inner();
            let _lifecycle = rt.1.lock().await;
            {
                let mut inner = runtime.0.lock().expect("mobile sync state lock");
                if inner.intent_generation != generation || inner.follow_host != follow || inner.starting {
                    continue; // 그 사이 사용자가 중지·시작·로그아웃함
                }
                inner.follow_host = Some(next.clone()); // 새 주소마다 한 번만 시도
            }
            if let Err(message) = mobile_sync_stop_internal(&runtime) {
                // 옛 서버를 못 끄면 새로 띄우지 않는다(두 서버 공존·포트 충돌 방지).
                runtime.0.lock().expect("mobile sync state lock").last_error =
                    Some(format!("네트워크 변경 후 자동 재시작 보류: {message}"));
                continue;
            }
            if let Err(message) =
                start_with_intent(app.clone(), profile, runtime, StartIntent::Follow(generation)).await
            {
                eprintln!("[mobile-sync] 네트워크 변경 후 자동 재시작 실패 ({next}): {message}");
            }
        }
    });
}

pub(crate) struct MobileSyncRuntime(Mutex<MobileSyncInner>, tokio::sync::Mutex<()>);

impl Default for MobileSyncRuntime {
    fn default() -> Self {
        Self(Mutex::new(MobileSyncInner::default()), tokio::sync::Mutex::new(()))
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MobileSyncStatus {
    running: bool,
    starting: bool,
    url: Option<String>,
    host: Option<String>,
    port: u16,
    certificate_fingerprint: Option<String>,
    certificate_expires_at: Option<i64>,
    last_error: Option<String>,
}

#[derive(Deserialize)]
struct CertificateOutput {
    cert_path: String,
    key_path: String,
    fingerprint: String,
    expires_at: i64,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PairingCodeOutput {
    code: String,
    expires_at: i64,
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct MobileStartupPreference {
    restore_on_app_start: bool,
}

impl MobileStartupPreference {
    fn record_manual_stop(&mut self) {
        self.restore_on_app_start = false;
    }
}

#[derive(Serialize, Deserialize)]
pub(crate) struct MobileDevice {
    id: String,
    name: String,
    created_at: i64,
    revoked_at: Option<i64>,
}

fn detected_private_ipv4() -> Result<String, String> {
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))
        .map_err(|error| format!("PC 네트워크 주소 확인 실패: {error}"))?;
    socket
        .connect((Ipv4Addr::new(192, 0, 2, 1), 9))
        .map_err(|error| format!("PC 네트워크 주소 확인 실패: {error}"))?;
    match socket
        .local_addr()
        .map_err(|error| format!("PC 네트워크 주소 확인 실패: {error}"))?
        .ip()
    {
        IpAddr::V4(address) if address.is_private() && !address.is_loopback() => {
            Ok(address.to_string())
        }
        _ => Err("같은 Wi-Fi 또는 핫스팟에서 사용할 private IPv4 주소를 찾지 못했습니다".into()),
    }
}

fn mobile_state_dir(profile: &RuntimeProfile) -> PathBuf {
    profile.config_dir.join("mobile-sync")
}

fn mobile_startup_preference_path(profile: &RuntimeProfile) -> PathBuf {
    mobile_state_dir(profile).join("startup-preference.json")
}

fn mobile_instance_id(profile: &RuntimeProfile) -> Result<String, String> {
    let directory = mobile_state_dir(profile);
    std::fs::create_dir_all(&directory)
        .map_err(|error| format!("모바일 발견 identity 폴더 생성 실패: {error}"))?;
    let path = directory.join("pc-instance-id");
    if let Ok(value) = std::fs::read_to_string(&path) {
        let value = value.trim();
        if is_valid_instance_id(value) {
            return Ok(value.to_owned());
        }
        return Err("저장된 모바일 발견 identity가 올바르지 않습니다".into());
    }
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes)
        .map_err(|error| format!("모바일 발견 identity 생성 실패: {error}"))?;
    let value = bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let temporary = directory.join("pc-instance-id.tmp");
    std::fs::write(&temporary, value.as_bytes())
        .map_err(|error| format!("모바일 발견 identity 임시 저장 실패: {error}"))?;
    std::fs::rename(&temporary, &path)
        .map_err(|error| format!("모바일 발견 identity 저장 실패: {error}"))?;
    Ok(value)
}

fn read_mobile_startup_preference(profile: &RuntimeProfile) -> MobileStartupPreference {
    std::fs::read(mobile_startup_preference_path(profile))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn write_mobile_startup_preference(
    profile: &RuntimeProfile,
    preference: &MobileStartupPreference,
) -> Result<(), String> {
    let directory = mobile_state_dir(profile);
    std::fs::create_dir_all(&directory)
        .map_err(|error| format!("모바일 연결 설정 폴더 생성 실패: {error}"))?;
    let payload = serde_json::to_vec(preference)
        .map_err(|error| format!("모바일 연결 설정 직렬화 실패: {error}"))?;
    std::fs::write(mobile_startup_preference_path(profile), payload)
        .map_err(|error| format!("모바일 연결 설정 저장 실패: {error}"))
}

fn should_restore_mobile_sync(enabled: bool, authenticated_context_ready: bool) -> bool {
    enabled && authenticated_context_ready
}

fn mobile_database(profile: &RuntimeProfile) -> PathBuf {
    profile.data_dir.join("customers.sqlite3")
}

fn prepare_admin_secret_path(state_dir: &Path) -> Result<PathBuf, String> {
    std::fs::create_dir_all(state_dir)
        .map_err(|error| format!("모바일 연결 설정 폴더 생성 실패: {error}"))?;
    Ok(state_dir.join("admin-api.secret"))
}

async fn admin_output(
    app: &tauri::AppHandle,
    profile: &RuntimeProfile,
    arguments: Vec<String>,
) -> Result<String, String> {
    let mut command = app
        .shell()
        .sidecar("local-engine")
        .map_err(|error| format!("번들 엔진을 찾을 수 없습니다: {error}"))?
        .env("CUSTOMER_DB_PATH", mobile_database(profile))
        .env("CUSTOMER_DB_KEYFILE", profile.customer_key_path())
        .env("PYTHONUTF8", "1")
        .env(
            "INSURANCE_AI_API_SECRET_FILE",
            prepare_admin_secret_path(&mobile_state_dir(profile))?,
        );
    for &(name, value) in mobile_keyfile_environment(profile.qa) {
        command = command.env(name, value);
    }
    let output = command
        .args(arguments)
        .output()
        .await
        .map_err(|error| format!("모바일 연결 관리 명령 실행 실패: {error}"))?;
    if !output.status.success() {
        return Err(mobile_stderr_category(&output.stderr)
            .unwrap_or("모바일 연결 관리 명령이 실패했습니다. 앱 로그를 확인하세요")
            .into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn current_status(inner: &MobileSyncInner) -> MobileSyncStatus {
    let running = inner
        .host
        .as_deref()
        .map(|host| is_tcp_port_open(host, MOBILE_SYNC_PORT, Duration::from_millis(300)))
        .unwrap_or(false)
        && inner.child.is_some();
    MobileSyncStatus {
        running,
        starting: inner.starting,
        url: inner
            .host
            .as_ref()
            .map(|host| format!("https://{host}:{MOBILE_SYNC_PORT}")),
        host: inner.host.clone(),
        port: MOBILE_SYNC_PORT,
        certificate_fingerprint: inner.fingerprint.clone(),
        certificate_expires_at: inner.certificate_expires_at,
        last_error: inner.last_error.clone(),
    }
}

#[tauri::command]
pub(crate) fn mobile_sync_status(state: tauri::State<'_, MobileSyncRuntime>) -> MobileSyncStatus {
    current_status(&state.0.lock().expect("mobile sync state lock"))
}

fn fail_start(state: &MobileSyncRuntime, message: String) -> String {
    let mut inner = state.0.lock().expect("mobile sync state lock");
    inner.starting = false;
    inner.last_error = Some(message.clone());
    message
}

#[derive(Debug, PartialEq)]
enum StartGate {
    ReturnRunning,
    RejectStarting,
    Proceed,
}

fn start_gate(running: bool, starting: bool) -> StartGate {
    if running {
        StartGate::ReturnRunning
    } else if starting {
        StartGate::RejectStarting
    } else {
        StartGate::Proceed
    }
}

#[derive(Debug, PartialEq)]
enum MobileStartDecision {
    Ready,
    Wait,
    TimedOut,
    Failed(String),
}

fn mobile_start_decision(
    elapsed: Duration,
    port_open: bool,
    child_present: bool,
    last_error: Option<&str>,
) -> MobileStartDecision {
    if !child_present {
        return MobileStartDecision::Failed(
            last_error
                .unwrap_or("모바일 연결 서버 child가 준비 전에 종료되었습니다")
                .to_owned(),
        );
    }
    if elapsed >= START_TIMEOUT {
        MobileStartDecision::TimedOut
    } else if port_open {
        MobileStartDecision::Ready
    } else {
        MobileStartDecision::Wait
    }
}

fn mobile_keyfile_environment(qa: bool) -> &'static [(&'static str, &'static str)] {
    if qa {
        &[("CUSTOMER_DB_KEYFILE_ONLY", "1")]
    } else {
        &[]
    }
}

fn mobile_stderr_category(stderr: &[u8]) -> Option<&'static str> {
    let text = String::from_utf8_lossy(stderr).to_ascii_lowercase();
    if text.contains("the following arguments are required") {
        Some("필수 실행 인자가 전달되지 않았습니다")
    } else if text.contains("unrecognized arguments") {
        Some("지원하지 않는 실행 인자가 전달되었습니다")
    } else if text.contains("tls file does not exist") {
        Some("TLS 인증서 또는 키 파일을 읽을 수 없습니다")
    } else if text.contains("certificate confirmation required") {
        Some("PC IP가 변경되어 기존 인증서를 보존했습니다. 인증서 변경 확인이 필요합니다")
    } else if text.contains("address already in use")
        || text.contains("only one usage of each socket address")
    {
        Some("모바일 연결 포트를 사용할 수 없습니다")
    } else if text.contains("traceback (most recent call last)") {
        Some("모바일 연결 서버 초기화 오류가 발생했습니다")
    } else {
        None
    }
}

fn certificate_admin_arguments(state_dir: &Path, host: &str, database: &Path) -> Vec<String> {
    vec![
        "--lan-sync-admin".into(),
        "--db".into(),
        database.to_string_lossy().into_owned(),
        "ensure-certificate".into(),
        "--state-dir".into(),
        state_dir.to_string_lossy().into_owned(),
        "--host".into(),
        host.into(),
    ]
}

#[tauri::command]
pub(crate) async fn mobile_sync_start(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<MobileSyncStatus, String> {
    let rt: &MobileSyncRuntime = state.inner();
    let _lifecycle = rt.1.lock().await;
    start_with_intent(app, profile, state, StartIntent::User).await
}

async fn start_with_intent(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
    intent: StartIntent,
) -> Result<MobileSyncStatus, String> {
    let generation;
    {
        let mut inner = state.0.lock().expect("mobile sync state lock");
        let status = current_status(&inner);
        match start_gate(status.running, inner.starting) {
            StartGate::ReturnRunning => return Ok(status),
            StartGate::RejectStarting => return Err("모바일 연결 서버가 이미 시작 중입니다".into()),
            StartGate::Proceed => {}
        }
        generation = begin_start_intent(&mut inner, intent)?;
        if inner.child.is_some() {
            if let Some(child) = inner.child.take() {
                let _ = child.kill();
            }
            if let Some(job) = inner.job.take() {
                job.terminate_all();
            }
            if let Some(discovery) = inner.discovery.take() {
                discovery.stop();
            }
        }
        inner.starting = true;
        inner.last_error = None;
    }

    let host = detected_private_ipv4().map_err(|message| fail_start(&state, message))?;
    let instance_id =
        mobile_instance_id(&profile).map_err(|message| fail_start(&state, message))?;
    if is_tcp_port_open(&host, MOBILE_SYNC_PORT, Duration::from_millis(300)) {
        return Err(fail_start(
            &state,
            format!("{host}:{MOBILE_SYNC_PORT} 포트를 다른 프로세스가 사용 중입니다"),
        ));
    }
    let state_dir = mobile_state_dir(&profile);
    std::fs::create_dir_all(&state_dir)
        .map_err(|error| fail_start(&state, format!("모바일 연결 설정 폴더 생성 실패: {error}")))?;
    let database = mobile_database(&profile);
    if !database.is_file() {
        return Err(fail_start(
            &state,
            "고객 DB가 준비되지 않아 모바일 연결을 시작할 수 없습니다".into(),
        ));
    }
    let cert_text = admin_output(
        &app,
        &profile,
        certificate_admin_arguments(&state_dir, &host, &database),
    )
    .await
    .map_err(|message| fail_start(&state, message))?;
    let certificate_json = cert_text
        .lines()
        .rev()
        .find(|line| line.trim_start().starts_with('{'))
        .ok_or_else(|| fail_start(&state, "인증서 준비 응답이 비어 있습니다".into()))?;
    let certificate: CertificateOutput = serde_json::from_str(certificate_json.trim())
        .map_err(|_| fail_start(&state, "인증서 준비 응답을 해석할 수 없습니다".into()))?;

    let shutdown_file = state_dir.join("stop.request");
    let _ = std::fs::remove_file(&shutdown_file);
    let mut command = app
        .shell()
        .sidecar("local-engine")
        .map_err(|error| fail_start(&state, format!("번들 엔진을 찾을 수 없습니다: {error}")))?
        .env("CUSTOMER_DB_PATH", &database)
        .env("CUSTOMER_DB_KEYFILE", profile.customer_key_path())
        .env("PYTHONUTF8", "1")
        .env(
            "INSURANCE_AI_API_SECRET_FILE",
            state_dir.join("server-api.secret"),
        )
        .env("APP_LOCK_FILE", profile.lock_path())
        .args([
            "--lan-sync-server",
            "--db",
            database.to_string_lossy().as_ref(),
            "--cert",
            &certificate.cert_path,
            "--key",
            &certificate.key_path,
            "--host",
            &host,
            "--port",
            &MOBILE_SYNC_PORT.to_string(),
            "--instance-id",
            &instance_id,
            "--shutdown-file",
            shutdown_file.to_string_lossy().as_ref(),
        ]);
    if profile.qa {
        command = command.env("INSURANCE_AI_QA_MODE", "1");
    }
    for &(name, value) in mobile_keyfile_environment(profile.qa) {
        command = command.env(name, value);
    }
    let (mut events, child) = command
        .spawn()
        .map_err(|error| fail_start(&state, format!("모바일 연결 서버 실행 실패: {error}")))?;
    let job = SidecarJob::new().map_err(|error| {
        fail_start(
            &state,
            format!("모바일 연결 process guard 생성 실패: {error}"),
        )
    })?;
    if let Err(error) = job.assign(child.pid()) {
        let _ = child.kill();
        job.terminate_all();
        return Err(fail_start(
            &state,
            format!("모바일 연결 process guard 적용 실패: {error}"),
        ));
    }
    {
        let mut inner = state.0.lock().expect("mobile sync state lock");
        inner.child = Some(child);
        inner.job = Some(job);
        inner.host = Some(host.clone());
        inner.fingerprint = Some(certificate.fingerprint);
        inner.certificate_expires_at = Some(certificate.expires_at);
        inner.shutdown_file = Some(shutdown_file);
    }

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut stderr_category = None;
        while let Some(event) = events.recv().await {
            let failure = match event {
                CommandEvent::Stderr(line) => {
                    if let Some(category) = mobile_stderr_category(&line) {
                        stderr_category = Some(category);
                    }
                    None
                }
                CommandEvent::Terminated(status) => Some(match stderr_category {
                    Some(category) => format!(
                        "모바일 연결 서버가 준비 전에 종료되었습니다 ({category}; code={})",
                        status
                            .code
                            .map(|code| code.to_string())
                            .unwrap_or_else(|| "unknown".into())
                    ),
                    None => format!(
                        "모바일 연결 서버가 준비 전에 종료되었습니다 (code={})",
                        status
                            .code
                            .map(|code| code.to_string())
                            .unwrap_or_else(|| "unknown".into())
                    ),
                }),
                CommandEvent::Error(_) => {
                    Some("모바일 연결 서버 process 오류가 발생했습니다".into())
                }
                _ => None,
            };
            if let Some(message) = failure {
                if let Some(runtime) = app_handle.try_state::<MobileSyncRuntime>() {
                    let mut inner = runtime.0.lock().expect("mobile sync state lock");
                    let was_unexpected = inner.child.take().is_some();
                    if let Some(job) = inner.job.take() {
                        job.terminate_all();
                    }
                    if let Some(discovery) = inner.discovery.take() {
                        discovery.stop();
                    }
                    inner.starting = false;
                    if was_unexpected {
                        inner.last_error = Some(message);
                    }
                }
                break;
            }
        }
    });

    let started = Instant::now();
    loop {
        let remaining = START_TIMEOUT.saturating_sub(started.elapsed());
        let port_open = !remaining.is_zero()
            && is_tcp_port_open(
                &host,
                MOBILE_SYNC_PORT,
                remaining.min(Duration::from_millis(300)),
            );
        let decision = {
            let inner = state.0.lock().expect("mobile sync state lock");
            mobile_start_decision(
                started.elapsed(),
                port_open,
                inner.child.is_some(),
                inner.last_error.as_deref(),
            )
        };
        match decision {
            MobileStartDecision::Ready => {
                let mut inner = state.0.lock().expect("mobile sync state lock");
                inner.starting = false;
                if inner.intent_generation != generation {
                    // 시작 도중 사용자가 중지·로그아웃했다 → 그 의도를 따른다.
                    drop(inner);
                    let _ = mobile_sync_stop_internal(&state);
                    return Err(FOLLOW_CANCELLED.into());
                }
                inner.follow_host = Some(host.clone());
                match MobileDiscoveryAdvertisement::start(&host, &instance_id) {
                    Ok(discovery) => inner.discovery = Some(discovery),
                    Err(error) => inner.last_error = Some(error),
                }
                return Ok(current_status(&inner));
            }
            MobileStartDecision::Failed(message) => {
                let _ = mobile_sync_stop_internal(&state);
                return Err(fail_start(&state, message));
            }
            MobileStartDecision::TimedOut => {
                let _ = mobile_sync_stop_internal(&state);
                return Err(fail_start(
                    &state,
                    "모바일 연결 서버가 제한 시간 안에 시작되지 않았습니다".into(),
                ));
            }
            MobileStartDecision::Wait => {
                let remaining = START_TIMEOUT.saturating_sub(started.elapsed());
                if !remaining.is_zero() {
                    tokio::time::sleep(remaining.min(Duration::from_millis(150))).await;
                }
            }
        }
    }
}

#[tauri::command]
pub(crate) fn mobile_sync_startup_preference(
    profile: tauri::State<'_, RuntimeProfile>,
) -> MobileStartupPreference {
    read_mobile_startup_preference(&profile)
}

#[tauri::command]
pub(crate) fn mobile_sync_set_startup_preference(
    profile: tauri::State<'_, RuntimeProfile>,
    enabled: bool,
) -> Result<MobileStartupPreference, String> {
    let preference = MobileStartupPreference {
        restore_on_app_start: enabled,
    };
    write_mobile_startup_preference(&profile, &preference)?;
    Ok(preference)
}

#[tauri::command]
pub(crate) async fn mobile_sync_restore_if_enabled(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<MobileSyncStatus, String> {
    let preference = read_mobile_startup_preference(&profile);
    if !should_restore_mobile_sync(preference.restore_on_app_start, true) {
        return Ok(current_status(
            &state.0.lock().expect("mobile sync state lock"),
        ));
    }
    mobile_sync_start(app, profile, state).await
}

fn mobile_sync_stop_internal(state: &MobileSyncRuntime) -> Result<MobileSyncStatus, String> {
    let (child, job, host, shutdown_file, discovery) = {
        let mut inner = state.0.lock().expect("mobile sync state lock");
        inner.starting = false;
        (
            inner.child.take(),
            inner.job.take(),
            inner.host.clone(),
            inner.shutdown_file.take(),
            inner.discovery.take(),
        )
    };
    if let Some(discovery) = discovery {
        discovery.stop();
    }
    if let Some(path) = shutdown_file {
        let _ = std::fs::write(path, b"stop");
    }
    if let Some(host) = &host {
        let graceful_deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < graceful_deadline
            && is_tcp_port_open(host, MOBILE_SYNC_PORT, Duration::from_millis(200))
        {
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    if let Some(child) = child {
        let _ = child.kill();
    }
    if let Some(job) = job {
        job.terminate_all();
    }
    if let Some(host) = host {
        let deadline = Instant::now() + STOP_TIMEOUT;
        while Instant::now() < deadline {
            if !is_tcp_port_open(&host, MOBILE_SYNC_PORT, Duration::from_millis(200)) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if is_tcp_port_open(&host, MOBILE_SYNC_PORT, Duration::from_millis(200)) {
            let message = "모바일 연결 서버의 대상 process tree를 종료하지 못했습니다".to_owned();
            state.0.lock().expect("mobile sync state lock").last_error = Some(message.clone());
            return Err(message);
        }
    }
    let mut inner = state.0.lock().expect("mobile sync state lock");
    inner.last_error = None;
    Ok(current_status(&inner))
}

#[tauri::command]
pub(crate) fn mobile_sync_stop(
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<MobileSyncStatus, String> {
    let mut preference = read_mobile_startup_preference(&profile);
    preference.record_manual_stop();
    write_mobile_startup_preference(&profile, &preference)?;
    // 사용자가 끈 연결은 네트워크가 바뀌어도 다시 켜지 않는다.
    end_user_intent(&mut state.0.lock().expect("mobile sync state lock"));
    mobile_sync_stop_internal(&state)
}

/// 로그아웃: 서버를 내리고 자동 추종을 끈다. '시작 시 복원' 설정은 유지해 다음 로그인 때만 다시 켠다.
#[tauri::command]
pub(crate) fn mobile_sync_pause_for_logout(
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<MobileSyncStatus, String> {
    end_user_intent(&mut state.0.lock().expect("mobile sync state lock"));
    mobile_sync_stop_internal(&state)
}

#[tauri::command]
pub(crate) async fn mobile_sync_create_pairing_code(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<PairingCodeOutput, String> {
    if !current_status(&state.0.lock().expect("mobile sync state lock")).running {
        return Err("모바일 연결 서버를 먼저 시작하세요".into());
    }
    issue_pairing_code(&app, &profile).await
}

/// QR 최초 등록 payload 형식. 폰(Android PairingQr.kt)과 반드시 같은 값이어야 한다.
const PAIRING_QR_KIND: &str = "moadoom-pair";
const PAIRING_QR_VERSION: u8 = 1;

/// QR에 담는 값: PC 주소(private IPv4 HTTPS), 공개 인증서 지문, 5분 1회용 코드, 만료시각, PC 발견 identity.
/// 장기 토큰·개인키·고객정보는 넣지 않는다. 입력이 하나라도 형식에 맞지 않으면 QR을 만들지 않는다.
fn pairing_qr_payload(
    host: &str,
    port: u16,
    fingerprint: &str,
    code: &str,
    expires_at: i64,
    instance_id: &str,
) -> Result<String, String> {
    let address: Ipv4Addr = host
        .parse()
        .map_err(|_| "QR에 넣을 PC 주소가 IPv4가 아닙니다".to_owned())?;
    if !address.is_private() || address.is_loopback() {
        return Err("QR에는 같은 Wi-Fi/핫스팟의 private IPv4 주소만 넣을 수 있습니다".into());
    }
    let fingerprint_ok = fingerprint.len() == 64
        && fingerprint
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
    if !fingerprint_ok {
        return Err("QR에 넣을 인증서 지문 형식이 올바르지 않습니다".into());
    }
    if code.len() != 6 || !code.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("QR에 넣을 1회용 코드 형식이 올바르지 않습니다".into());
    }
    if !is_valid_instance_id(instance_id) || expires_at <= 0 || port == 0 {
        return Err("QR에 넣을 PC 정보가 올바르지 않습니다".into());
    }
    Ok(serde_json::json!({
        "t": PAIRING_QR_KIND,
        "v": PAIRING_QR_VERSION,
        "u": format!("https://{host}:{port}"),
        "f": fingerprint,
        "c": code,
        "e": expires_at,
        "i": instance_id,
    })
    .to_string())
}

fn pairing_qr_code(payload: &str) -> Result<qrcode::QrCode, String> {
    qrcode::QrCode::with_error_correction_level(payload.as_bytes(), qrcode::EcLevel::M)
        .map_err(|error| format!("QR 생성 실패: {error}"))
}

/// 외부 서비스 없이 앱 안에서 SVG로 그린다.
fn pairing_qr_svg(payload: &str) -> Result<String, String> {
    Ok(pairing_qr_code(payload)?
        .render::<qrcode::render::svg::Color>()
        .min_dimensions(280, 280)
        .quiet_zone(true)
        .dark_color(qrcode::render::svg::Color("#000000"))
        .light_color(qrcode::render::svg::Color("#ffffff"))
        .build())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PairingQrOutput {
    svg: String,
    code: String,
    expires_at: i64,
    payload_version: u8,
    url: String,
    certificate_fingerprint: String,
}

#[tauri::command]
pub(crate) async fn mobile_sync_create_pairing_qr(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
) -> Result<PairingQrOutput, String> {
    let snapshot = || {
        let inner = state.0.lock().expect("mobile sync state lock");
        let status = current_status(&inner);
        (status.running, inner.host.clone(), inner.fingerprint.clone())
    };
    let (running, host, fingerprint) = snapshot();
    let (Some(host), Some(fingerprint), true) = (host, fingerprint, running) else {
        return Err("모바일 연결이 준비된 뒤 QR을 만들 수 있습니다".into());
    };
    let instance_id = mobile_instance_id(&profile)?;
    let issued = issue_pairing_code(&app, &profile).await?;
    // 코드 발급 중 네트워크 추종으로 주소·인증서가 바뀌었으면 옛 주소 QR을 보여주지 않는다.
    let (_, host_now, fingerprint_now) = snapshot();
    if host_now.as_deref() != Some(host.as_str()) || fingerprint_now.as_deref() != Some(fingerprint.as_str()) {
        return Err("PC 주소가 방금 바뀌었습니다. QR을 다시 만들어 주세요".into());
    }
    let payload = pairing_qr_payload(
        &host,
        MOBILE_SYNC_PORT,
        &fingerprint,
        &issued.code,
        issued.expires_at,
        &instance_id,
    )?;
    Ok(PairingQrOutput {
        svg: pairing_qr_svg(&payload)?,
        code: issued.code,
        expires_at: issued.expires_at,
        payload_version: PAIRING_QR_VERSION,
        url: format!("https://{host}:{MOBILE_SYNC_PORT}"),
        certificate_fingerprint: fingerprint,
    })
}

async fn issue_pairing_code(
    app: &tauri::AppHandle,
    profile: &RuntimeProfile,
) -> Result<PairingCodeOutput, String> {
    let computer = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "내 PC".into());
    let output = admin_output(
        app,
        profile,
        vec![
            "--lan-sync-admin".into(),
            "--db".into(),
            mobile_database(profile).to_string_lossy().into_owned(),
            "create-code".into(),
            "--pc-name".into(),
            computer,
            "--ttl".into(),
            "300".into(),
        ],
    )
    .await?;
    let values: std::collections::HashMap<_, _> = output
        .lines()
        .filter_map(|line| line.split_once('='))
        .collect();
    let code = values
        .get("pairing_code")
        .ok_or("페어링 코드 응답이 올바르지 않습니다")?
        .to_string();
    let expires_at = values
        .get("expires_at")
        .ok_or("페어링 만료 응답이 올바르지 않습니다")?
        .parse()
        .map_err(|_| "페어링 만료 응답이 올바르지 않습니다")?;
    Ok(PairingCodeOutput { code, expires_at })
}

#[tauri::command]
pub(crate) async fn mobile_sync_create_recovery_code(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    state: tauri::State<'_, MobileSyncRuntime>,
    device_id: String,
    confirmed: bool,
) -> Result<PairingCodeOutput, String> {
    if !confirmed {
        return Err("로컬 관리자 확인 후에만 복구 코드를 만들 수 있습니다".into());
    }
    if !current_status(&state.0.lock().expect("mobile sync state lock")).running {
        return Err("모바일 연결 서버를 먼저 시작하세요".into());
    }
    if device_id.len() > 64
        || !device_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        return Err("올바르지 않은 기기 식별자입니다".into());
    }
    let instance_id = mobile_instance_id(&profile)?;
    let output = admin_output(
        &app,
        &profile,
        vec![
            "--lan-sync-admin".into(),
            "--db".into(),
            mobile_database(&profile).to_string_lossy().into_owned(),
            "create-recovery-code".into(),
            device_id,
            "--pc-instance-id".into(),
            instance_id,
            "--ttl".into(),
            "120".into(),
        ],
    )
    .await?;
    let values: std::collections::HashMap<_, _> = output
        .lines()
        .filter_map(|line| line.split_once('='))
        .collect();
    let code = values
        .get("recovery_code")
        .ok_or("복구 코드 응답이 올바르지 않습니다")?
        .to_string();
    let expires_at = values
        .get("expires_at")
        .ok_or("복구 코드 만료 응답이 올바르지 않습니다")?
        .parse()
        .map_err(|_| "복구 코드 만료 응답이 올바르지 않습니다")?;
    Ok(PairingCodeOutput { code, expires_at })
}

#[tauri::command]
pub(crate) async fn mobile_sync_list_devices(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<Vec<MobileDevice>, String> {
    let output = admin_output(
        &app,
        &profile,
        vec![
            "--lan-sync-admin".into(),
            "--db".into(),
            mobile_database(&profile).to_string_lossy().into_owned(),
            "list-devices".into(),
        ],
    )
    .await?;
    let payload = output
        .lines()
        .rev()
        .find(|line| line.trim_start().starts_with('['))
        .ok_or("등록 기기 응답이 비어 있습니다")?;
    serde_json::from_str(payload.trim()).map_err(|_| "등록 기기 응답을 해석할 수 없습니다".into())
}

#[tauri::command]
pub(crate) async fn mobile_sync_revoke_device(
    app: tauri::AppHandle,
    profile: tauri::State<'_, RuntimeProfile>,
    device_id: String,
) -> Result<(), String> {
    if device_id.len() > 64
        || !device_id
            .chars()
            .all(|character| character.is_ascii_hexdigit() || character == '-')
    {
        return Err("올바르지 않은 기기 식별자입니다".into());
    }
    admin_output(
        &app,
        &profile,
        vec![
            "--lan-sync-admin".into(),
            "--db".into(),
            mobile_database(&profile).to_string_lossy().into_owned(),
            "revoke".into(),
            device_id,
        ],
    )
    .await?;
    Ok(())
}

#[tauri::command]
pub(crate) async fn mobile_sync_firewall_status(
    profile: tauri::State<'_, RuntimeProfile>,
) -> Result<crate::mobile_firewall::FirewallStatus, String> {
    let qa = profile.qa;
    tauri::async_runtime::spawn_blocking(move || {
        let host = detected_private_ipv4().ok();
        crate::mobile_firewall::status(qa, host.as_deref())
    })
    .await
    .map_err(|error| format!("방화벽 상태 확인 실패: {error}"))
}

#[tauri::command]
pub(crate) async fn mobile_sync_firewall_apply(
    profile: tauri::State<'_, RuntimeProfile>,
    enable: bool,
    confirmed: bool,
) -> Result<crate::mobile_firewall::FirewallStatus, String> {
    let qa = profile.qa;
    tauri::async_runtime::spawn_blocking(move || {
        let host = detected_private_ipv4().ok();
        crate::mobile_firewall::apply(qa, confirmed, enable, host.as_deref())
    })
    .await
    .map_err(|error| format!("방화벽 변경 작업 실패: {error}"))
}

pub(crate) fn terminate(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<MobileSyncRuntime>() {
        let _ = mobile_sync_stop_internal(&state);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const QR_FP: &str = "7502f13db142ce04e5caf9ebf36feb299ff1a25fc3796240979c48cef991b432";
    const QR_INSTANCE: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn pairing_qr_payload_has_only_bootstrap_fields() {
        let payload = pairing_qr_payload("192.168.0.73", 18443, QR_FP, "042917", 1_794_200_000, QR_INSTANCE)
            .expect("payload");
        let value: serde_json::Value = serde_json::from_str(&payload).expect("json");
        let object = value.as_object().expect("object");
        let mut keys: Vec<_> = object.keys().cloned().collect();
        keys.sort();
        assert_eq!(keys, ["c", "e", "f", "i", "t", "u", "v"]);
        assert_eq!(value["t"], "moadoom-pair");
        assert_eq!(value["v"], 1);
        assert_eq!(value["u"], "https://192.168.0.73:18443");
        assert_eq!(value["c"], "042917");
        assert!(payload.len() < 300, "QR payload stays small: {}", payload.len());
        for forbidden in ["token", "key", "secret", "BEGIN", "customer"] {
            assert!(!payload.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn pairing_qr_payload_rejects_unsafe_inputs() {
        let ok = |host: &str, fp: &str, code: &str| pairing_qr_payload(host, 18443, fp, code, 1, QR_INSTANCE);
        assert!(ok("8.8.8.8", QR_FP, "123456").is_err());
        assert!(ok("127.0.0.1", QR_FP, "123456").is_err());
        assert!(ok("pc.example.com", QR_FP, "123456").is_err());
        assert!(ok("192.168.0.73", &QR_FP[..63], "123456").is_err());
        assert!(ok("192.168.0.73", &QR_FP.to_uppercase(), "123456").is_err());
        assert!(ok("192.168.0.73", QR_FP, "12345").is_err());
        assert!(ok("192.168.0.73", QR_FP, "12345a").is_err());
        assert!(pairing_qr_payload("10.0.0.2", 18443, QR_FP, "123456", 0, QR_INSTANCE).is_err());
        assert!(pairing_qr_payload("10.0.0.2", 18443, QR_FP, "123456", 1, "short").is_err());
        assert!(ok("172.20.10.2", QR_FP, "123456").is_ok());
    }

    #[test]
    fn pairing_qr_svg_is_local_self_contained_svg() {
        let payload = pairing_qr_payload("192.168.0.73", 18443, QR_FP, "042917", 1_794_200_000, QR_INSTANCE)
            .expect("payload");
        let svg = pairing_qr_svg(&payload).expect("svg");
        assert!(svg.contains("<svg"));
        assert!(!svg.contains("http://") || svg.contains("http://www.w3.org/2000/svg"));
        assert!(!svg.contains("042917"), "code must not appear as text in SVG");
        assert!(!svg.contains("<script"));
    }

    /// QR_ROUNDTRIP_OUT가 있으면 실제 모듈 행렬을 PGM 이미지로 써서 외부 디코더(OpenCV, ZXing)로 검증한다.
    #[test]
    fn pairing_qr_exports_pgm_for_external_decoder_roundtrip() {
        let Ok(directory) = std::env::var("QR_ROUNDTRIP_OUT") else { return };
        let payload = pairing_qr_payload("192.168.0.73", 18443, QR_FP, "042917", 1_794_200_000, QR_INSTANCE)
            .expect("payload");
        let code = pairing_qr_code(&payload).expect("qr");
        let width = code.width();
        let colors = code.to_colors();
        let (scale, quiet) = (4usize, 4usize);
        let side = (width + quiet * 2) * scale;
        let mut pixels = vec![255u8; side * side];
        for y in 0..width {
            for x in 0..width {
                if colors[y * width + x] == qrcode::Color::Dark {
                    for dy in 0..scale {
                        for dx in 0..scale {
                            let py = (y + quiet) * scale + dy;
                            let px = (x + quiet) * scale + dx;
                            pixels[py * side + px] = 0;
                        }
                    }
                }
            }
        }
        let mut image = format!("P5\n{side} {side}\n255\n").into_bytes();
        image.extend_from_slice(&pixels);
        let directory = PathBuf::from(directory);
        std::fs::create_dir_all(&directory).expect("dir");
        std::fs::write(directory.join("pairing-qr.pgm"), image).expect("pgm");
        std::fs::write(directory.join("pairing-qr.payload.json"), &payload).expect("payload");
        std::fs::write(directory.join("pairing-qr.svg"), pairing_qr_svg(&payload).expect("svg")).expect("svg");
    }

    #[test]
    fn admin_secret_parent_exists_before_spawning_sidecar() {
        let directory = std::env::temp_dir().join(format!(
            "moadoom-admin-secret-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        assert!(!directory.exists());

        let secret_path = prepare_admin_secret_path(&directory).expect("prepare state directory");

        assert!(directory.is_dir());
        assert_eq!(secret_path, directory.join("admin-api.secret"));
        std::fs::remove_dir_all(directory).expect("remove test directory");
    }

    #[test]
    fn duplicate_start_gate_reuses_running_and_rejects_concurrent_start() {
        assert_eq!(start_gate(true, false), StartGate::ReturnRunning);
        assert_eq!(start_gate(false, true), StartGate::RejectStarting);
        assert_eq!(start_gate(false, false), StartGate::Proceed);
    }

    #[test]
    fn certificate_admin_arguments_include_database_for_migration_gate() {
        let arguments = certificate_admin_arguments(
            Path::new(r"C:\state"),
            "192.168.50.11",
            Path::new(r"C:\profile\customers.db"),
        );
        assert_eq!(
            arguments,
            vec![
                "--lan-sync-admin",
                "--db",
                r"C:\profile\customers.db",
                "ensure-certificate",
                "--state-dir",
                r"C:\state",
                "--host",
                "192.168.50.11",
            ]
        );
    }

    #[test]
    fn mobile_start_accepts_ready_after_legacy_fifteen_second_boundary() {
        assert_eq!(START_TIMEOUT, Duration::from_secs(60));
        assert_eq!(
            mobile_start_decision(Duration::from_secs(20), true, true, None),
            MobileStartDecision::Ready
        );
    }

    #[test]
    fn mobile_start_times_out_at_sixty_second_deadline() {
        assert_eq!(
            mobile_start_decision(START_TIMEOUT, false, true, None),
            MobileStartDecision::TimedOut
        );
        assert_eq!(
            mobile_start_decision(START_TIMEOUT, true, true, None),
            MobileStartDecision::TimedOut
        );
    }

    #[test]
    fn mobile_start_reports_child_exit_before_timeout() {
        assert_eq!(
            mobile_start_decision(Duration::from_secs(3), false, false, None),
            MobileStartDecision::Failed("모바일 연결 서버 child가 준비 전에 종료되었습니다".into())
        );
    }

    #[test]
    fn mobile_start_preserves_specific_child_error() {
        let error = "모바일 연결 서버가 준비 전에 종료되었습니다 (code=7)";
        assert_eq!(
            mobile_start_decision(Duration::from_secs(3), false, false, Some(error)),
            MobileStartDecision::Failed(error.into())
        );
    }

    #[test]
    fn mobile_stderr_classifies_argparse_without_exposing_arguments() {
        assert_eq!(
            mobile_stderr_category(b"usage: local-engine.exe [-h]\nlocal-engine.exe: error: the following arguments are required: --db"),
            Some("필수 실행 인자가 전달되지 않았습니다")
        );
        assert_eq!(
            mobile_stderr_category(
                b"local-engine.exe: error: unrecognized arguments: --private-value"
            ),
            Some("지원하지 않는 실행 인자가 전달되었습니다")
        );
    }

    #[test]
    fn mobile_stderr_classifies_tls_and_runtime_failures_without_raw_output() {
        assert_eq!(
            mobile_stderr_category(
                b"local-engine.exe: error: TLS file does not exist: C:\\secret\\key.pem"
            ),
            Some("TLS 인증서 또는 키 파일을 읽을 수 없습니다")
        );
        assert_eq!(
            mobile_stderr_category(b"Traceback (most recent call last):\nprivate details"),
            Some("모바일 연결 서버 초기화 오류가 발생했습니다")
        );
        assert_eq!(mobile_stderr_category(b"arbitrary private output"), None);
        assert_eq!(
            mobile_stderr_category(b"ValueError: mobile sync address changed; certificate confirmation required before replacing the existing identity"),
            Some("PC IP가 변경되어 기존 인증서를 보존했습니다. 인증서 변경 확인이 필요합니다")
        );
    }

    #[test]
    fn qa_mobile_children_use_keyfile_only_without_changing_normal_policy() {
        assert_eq!(
            mobile_keyfile_environment(true),
            &[("CUSTOMER_DB_KEYFILE_ONLY", "1")]
        );
        assert!(mobile_keyfile_environment(false).is_empty());
    }

    #[test]
    fn network_follow_restarts_once_per_new_private_address_only_when_user_enabled() {
        // 사용자가 켠 적 없거나 수동 중지 → 아무것도 안 함
        assert_eq!(network_follow_action(None, false, Some("192.168.0.73")), NetworkFollowAction::Keep);
        // 주소 그대로 → 유지
        assert_eq!(
            network_follow_action(Some("192.168.0.73"), false, Some("192.168.0.73")),
            NetworkFollowAction::Keep
        );
        // Wi-Fi↔핫스팟 전환 중 주소 없음 → 기다림(서버 중지·인증서 변경 없음)
        assert_eq!(network_follow_action(Some("192.168.0.73"), false, None), NetworkFollowAction::Keep);
        // 시작 진행 중 → 중복 시작 안 함
        assert_eq!(
            network_follow_action(Some("192.168.0.73"), true, Some("172.20.10.4")),
            NetworkFollowAction::Keep
        );
        // 새 주소 → 그 주소로 한 번 재시작(호출 측이 follow_host를 새 주소로 바꿔 반복 시도 차단)
        assert_eq!(
            network_follow_action(Some("192.168.0.73"), false, Some("172.20.10.4")),
            NetworkFollowAction::Restart("172.20.10.4".into())
        );
        assert_eq!(
            network_follow_action(Some("172.20.10.4"), false, Some("172.20.10.4")),
            NetworkFollowAction::Keep
        );
    }

    #[test]
    fn network_follow_is_wired_to_user_start_and_manual_stop() {
        let source = include_str!("mobile_sync.rs");
        let ready = source.find("MobileStartDecision::Ready => {").expect("ready branch");
        let follow_set = source[ready..].find("inner.follow_host = Some(host.clone());").expect("follow set");
        assert!(follow_set < 700);
        let stop = source.find("pub(crate) fn mobile_sync_stop(").expect("manual stop");
        let follow_clear = source[stop..].find("end_user_intent(").expect("follow clear");
        assert!(follow_clear < 600);
        let pause = source.find("pub(crate) fn mobile_sync_pause_for_logout(").expect("logout pause");
        assert!(source[pause..].find("end_user_intent(").expect("pause clears") < 300);
        let lib = include_str!("lib.rs");
        assert!(lib.contains("mobile_sync::spawn_network_follow(app.handle().clone());"));
    }

    #[test]
    fn manual_stop_or_logout_cannot_be_overridden_by_pending_follow_restart() {
        let mut inner = MobileSyncInner::default();
        // 사용자 시작 → 세대 1, Ready에서 follow 설정
        let user = begin_start_intent(&mut inner, StartIntent::User).expect("user start");
        inner.follow_host = Some("192.168.0.73".into());
        // 모니터가 세대 1을 읽은 뒤 사용자가 중지(또는 로그아웃)
        let observed = inner.intent_generation;
        assert_eq!(observed, user);
        end_user_intent(&mut inner);
        assert!(inner.follow_host.is_none());
        // 지연된 자동 재시작은 진입에서 거부
        assert_eq!(
            begin_start_intent(&mut inner, StartIntent::Follow(observed)),
            Err(FOLLOW_CANCELLED.to_string())
        );
        // follow가 다시 켜져 있어도(이후 사용자 재시작) 옛 세대의 자동 재시작은 거부
        let again = begin_start_intent(&mut inner, StartIntent::User).expect("user restart");
        inner.follow_host = Some("172.20.10.4".into());
        assert!(again > observed);
        assert!(begin_start_intent(&mut inner, StartIntent::Follow(observed)).is_err());
        // 현재 세대의 자동 재시작은 허용(사용자 의도가 그대로일 때)
        assert_eq!(begin_start_intent(&mut inner, StartIntent::Follow(again)), Ok(again));
        // 중지 상태(follow 없음)에서는 같은 세대라도 자동 재시작 불가
        inner.follow_host = None;
        assert!(begin_start_intent(&mut inner, StartIntent::Follow(again)).is_err());
    }

    #[test]
    fn follow_restart_is_serialized_and_rechecks_intent_and_stop_failure() {
        let source = include_str!("mobile_sync.rs");
        let follow = source.find("pub(crate) fn spawn_network_follow(").expect("follow loop");
        let body = &source[follow..];
        let lock = body.find("rt.1.lock().await").expect("lifecycle lock");
        let recheck = body.find("inner.intent_generation != generation").expect("recheck");
        let stop = body.find("if let Err(message) = mobile_sync_stop_internal(&runtime)").expect("stop checked");
        let start = body.find("StartIntent::Follow(generation)").expect("follow start");
        assert!(lock < recheck && recheck < stop && stop < start);
        let user = source.find("pub(crate) async fn mobile_sync_start(").expect("user start");
        assert!(source[user..].find("rt.1.lock().await").expect("user lock") < 360);
        let ready = source.find("MobileStartDecision::Ready => {").expect("ready");
        assert!(source[ready..].find("inner.intent_generation != generation").expect("ready recheck") < 200);
    }

    #[test]
    fn startup_restore_is_opt_in_and_requires_ready_context() {
        assert!(!should_restore_mobile_sync(false, true));
        assert!(!should_restore_mobile_sync(true, false));
        assert!(should_restore_mobile_sync(true, true));
    }

    #[test]
    fn manual_stop_disables_future_startup_restore() {
        let mut preference = MobileStartupPreference {
            restore_on_app_start: true,
        };
        preference.record_manual_stop();
        assert!(!preference.restore_on_app_start);
    }

    #[test]
    fn discovery_advertises_only_protocol_and_stable_instance_identity() {
        let instance_id = "0123456789abcdef0123456789abcdef";
        let properties = discovery_txt_properties(instance_id);
        assert_eq!(properties.get("version"), Some(&"1".to_owned()));
        assert_eq!(properties.get("instance"), Some(&instance_id.to_owned()));
        assert_eq!(properties.len(), 2);
        assert!(!properties
            .keys()
            .any(|key| key.contains("token") || key.contains("pin")));
    }

    #[test]
    fn discovery_instance_identity_is_strictly_validated() {
        assert!(is_valid_instance_id("0123456789abcdef0123456789abcdef"));
        assert!(!is_valid_instance_id(""));
        assert!(!is_valid_instance_id("0123456789ABCDEF0123456789ABCDEF"));
        assert!(!is_valid_instance_id("0123456789abcdef0123456789abcdeg"));
    }

    #[test]
    fn windows_mdns_advertisement_is_discoverable_on_detected_private_interface() {
        let Ok(host) = detected_private_ipv4() else {
            return;
        };
        let browser = ServiceDaemon::new().expect("mDNS browser should start");
        let receiver = browser
            .browse(DISCOVERY_SERVICE_TYPE)
            .expect("mDNS browse should start");
        let advertisement =
            MobileDiscoveryAdvertisement::start(&host, "0123456789abcdef0123456789abcdef")
                .expect("mDNS advertisement should register");
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut resolved = false;
        while Instant::now() < deadline {
            if let Ok(mdns_sd::ServiceEvent::ServiceResolved(info)) =
                receiver.recv_timeout(Duration::from_millis(250))
            {
                if info.get_property_val_str("instance") == Some("0123456789abcdef0123456789abcdef")
                {
                    resolved = true;
                    break;
                }
            }
        }
        advertisement.stop();
        let _ = browser.stop_browse(DISCOVERY_SERVICE_TYPE);
        let _ = browser.shutdown();
        assert!(resolved, "registered mDNS service was not resolved");
    }
}
