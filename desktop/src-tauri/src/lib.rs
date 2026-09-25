use fs2::FileExt;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};
use tauri_plugin_updater::UpdaterExt;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![greet])
        .setup(|app| {
            let updater_app = app.handle().clone();
            std::thread::spawn(move || {
                // 프로덕션 패키지 실행 후 5초 뒤 업데이트 manifest 를 확인한다.
                // 설치 여부와 재시작 확인 다이얼로그는 프론트엔드(App.tsx)가 담당한다.
                std::thread::sleep(std::time::Duration::from_secs(5));
                tauri::async_runtime::block_on(async move {
                    match updater_app.updater() {
                        Ok(updater) => match updater.check().await {
                            Ok(Some(_)) => eprintln!("[updater] 업데이트 사용 가능"),
                            Ok(None) => eprintln!("[updater] 최신 버전"),
                            Err(e) => eprintln!("[updater] 확인 실패: {e}"),
                        },
                        Err(e) => eprintln!("[updater] 초기화 실패: {e}"),
                    }
                });
            });

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
            let (mut engine_events, engine_child) = app
                .shell()
                .sidecar("local-engine")?
                .env("CUSTOMER_DB_PATH", data_dir.join("customers.sqlite3"))
                .env("RAG_DB_PATH", data_dir.join("rag-index.sqlite3"))
                .env("WHISPER_MODEL_DIR", data_dir.join("whisper-models"))
                .env("CUSTOMER_DB_KEYFILE", config_dir.join("customer-db.key"))
                // whisper 모델(1.5GB+)은 부팅 시 받지 않는다 — 첫 STT 사용 시 자체 UI 로 다운로드
                .env("WHISPER_PREWARM", "0")
                // 앱이 죽으면 이 락이 풀리고 sidecar 가 자체 종료 (고아 방지)
                .env("APP_LOCK_FILE", &lock_path_str)
                .spawn()?;
            #[cfg(windows)]
            sidecar_job.assign(engine_child.pid())?;
            tauri::async_runtime::spawn(async move {
                while let Some(event) = engine_events.recv().await {
                    match event {
                        CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                            eprintln!("[local-engine] {}", String::from_utf8_lossy(&l).trim_end())
                        }
                        CommandEvent::Terminated(p) => eprintln!(
                            "[local-engine] 종료됨 code={:?} signal={:?}",
                            p.code, p.signal
                        ),
                        CommandEvent::Error(e) => eprintln!("[local-engine] 오류: {e}"),
                        _ => {}
                    }
                }
            });
            app.manage(SidecarChildren {
                control_server: Mutex::new(Some(child)),
                local_engine: Mutex::new(Some(engine_child)),
            });
            #[cfg(windows)]
            app.manage(sidecar_job);
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
