use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

struct ControlServerChild(Mutex<Option<CommandChild>>);

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
            let config_dir = app.path().app_config_dir()?;
            let data_dir = app.path().app_data_dir()?;
            fs::create_dir_all(&data_dir)?;
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
                // 부모(이 앱) PID → sidecar 가 부모 소멸 시 자체 종료 (고아 방지)
                .env("CONTROL_PARENT_PID", std::process::id().to_string())
                .spawn()?;
            // sidecar 출력을 앱 stderr 로 흘려 진단 가능하게 하고 조기 종료를 표면화한다.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Stdout(l) | CommandEvent::Stderr(l) => {
                            eprintln!("[control-server] {}", String::from_utf8_lossy(&l).trim_end())
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
            app.manage(ControlServerChild(Mutex::new(Some(child))));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(state) = app_handle.try_state::<ControlServerChild>() {
                if let Some(child) = state.0.lock().expect("control-server child lock").take() {
                    let _ = child.kill();
                }
            }
        }
    });
}
