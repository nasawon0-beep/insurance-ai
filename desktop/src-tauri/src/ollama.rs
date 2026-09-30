use futures_util::StreamExt;
use regex::Regex;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use tauri::{Emitter, Window};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};

#[cfg(windows)]
const WINDOWS_DOWNLOAD_URL: &str =
    "https://github.com/ollama/ollama/releases/download/v0.1.29/OllamaSetup.exe";
#[cfg(target_os = "macos")]
const MACOS_DOWNLOAD_URL: &str =
    "https://github.com/ollama/ollama/releases/download/v0.1.29/Ollama-darwin.zip";
const PROGRESS_EVENT: &str = "ollama-install-progress";
const MODEL_PROGRESS_EVENT: &str = "model-download-progress";
const REQUIRED_MODELS: [&str; 2] = ["bge-m3:latest", "qwen2.5:7b"];

#[derive(Clone, Serialize)]
struct OllamaInstallProgress {
    status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    progress: Option<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'static str>,
    message: String,
}

impl OllamaInstallProgress {
    fn progress(status: &'static str, progress: u8, message: impl Into<String>) -> Self {
        Self {
            status,
            progress: Some(progress),
            error: None,
            message: message.into(),
        }
    }
}

#[derive(Clone, Serialize)]
struct ModelDownloadProgress {
    model: String,
    status: &'static str,
    progress: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    downloaded: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    total: Option<String>,
    message: String,
}

struct ProgressInfo {
    percent: u32,
    downloaded: String,
    total: String,
}

#[derive(Debug)]
enum OllamaInstallError {
    NetworkError(String),
    PermissionError(String),
    DiskSpaceError(String),
    UnknownError(String),
}

impl OllamaInstallError {
    fn payload(&self) -> OllamaInstallProgress {
        match self {
            Self::NetworkError(message) => OllamaInstallProgress {
                status: "error",
                progress: None,
                error: Some("NetworkError"),
                message: format!("다운로드 실패: {message}"),
            },
            Self::PermissionError(message) => OllamaInstallProgress {
                status: "error",
                progress: None,
                error: Some("PermissionError"),
                message: format!("설치 권한 없음: {message}"),
            },
            Self::DiskSpaceError(message) => OllamaInstallProgress {
                status: "error",
                progress: None,
                error: Some("DiskSpaceError"),
                message: format!("디스크 용량 부족: {message}"),
            },
            Self::UnknownError(message) => OllamaInstallProgress {
                status: "error",
                progress: None,
                error: Some("UnknownError"),
                message: format!("알 수 없는 오류: {message}"),
            },
        }
    }

    fn message(&self) -> String {
        self.payload().message
    }
}

#[tauri::command]
pub async fn check_ollama_installed() -> Result<bool, String> {
    #[cfg(windows)]
    {
        return Ok(resolve_ollama_executable().is_some());
    }

    #[cfg(target_os = "macos")]
    {
        return Ok(Command::new("which")
            .arg("ollama")
            .status()
            .map(|status| status.success())
            .unwrap_or(false));
    }

    #[cfg(not(any(windows, target_os = "macos")))]
    {
        Ok(Command::new("which")
            .arg("ollama")
            .status()
            .map(|status| status.success())
            .unwrap_or(false))
    }
}

#[tauri::command]
pub async fn check_models_installed() -> Result<Vec<String>, String> {
    let output = ensure_ollama_ready().await?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(map_ollama_error_message(&stderr, "모델 확인 실패"));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(parse_installed_models(&stdout))
}

#[tauri::command]
pub async fn download_model(model_name: String, window: Window) -> Result<(), String> {
    ensure_ollama_ready().await?;

    emit_model_progress(
        &window,
        ModelDownloadProgress {
            model: model_name.clone(),
            status: "pulling",
            progress: 0,
            downloaded: None,
            total: None,
            message: format!("{} 다운로드 시작...", model_name),
        },
    )?;

    let ollama = ollama_executable_for_command();
    let mut child = tokio::process::Command::new(&ollama)
        .arg("pull")
        .arg(&model_name)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| "Ollama가 실행되지 않았습니다".to_string())?;

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    if let Some(stdout) = child.stdout.take() {
        stream_pull_output(stdout, tx.clone());
    }
    if let Some(stderr) = child.stderr.take() {
        stream_pull_output(stderr, tx.clone());
    }
    drop(tx);

    let mut output_lines = Vec::new();
    let status = loop {
        tokio::select! {
            status = child.wait() => {
                break status.map_err(|error| format!("대기 실패: {error}"))?;
            }
            Some(chunk) = rx.recv() => {
                for line in chunk.split(['\r', '\n']).map(str::trim).filter(|line| !line.is_empty()) {
                    output_lines.push(line.to_string());
                    handle_model_pull_line(&window, &model_name, line)?;
                }
            }
        }
    };

    if !status.success() {
        let message = map_ollama_error_message(&output_lines.join("\n"), "다운로드 실패");
        let _ = emit_model_progress(
            &window,
            ModelDownloadProgress {
                model: model_name,
                status: "error",
                progress: 0,
                downloaded: None,
                total: None,
                message: message.clone(),
            },
        );
        return Err(message);
    }

    emit_model_progress(
        &window,
        ModelDownloadProgress {
            model: model_name,
            status: "completed",
            progress: 100,
            downloaded: None,
            total: None,
            message: "완료".to_string(),
        },
    )?;

    Ok(())
}

#[tauri::command]
pub async fn install_ollama(window: Window) -> Result<(), String> {
    let install_window = window.clone();
    let install_result = tokio::spawn(async move { install_ollama_inner(&install_window).await })
        .await
        .map_err(|error| format!("알 수 없는 오류: 설치 작업 실행 실패: {error}"))?;

    match install_result {
        Ok(()) => Ok(()),
        Err(error) => {
            if let Err(emit_error) = emit_progress(&window, error.payload()) {
                return Err(emit_error.message());
            }
            Err(error.message())
        }
    }
}

async fn install_ollama_inner(window: &Window) -> Result<(), OllamaInstallError> {
    emit_progress(
        window,
        OllamaInstallProgress::progress("starting", 0, "Ollama 설치를 시작합니다"),
    )?;

    #[cfg(windows)]
    {
        install_ollama_windows(window).await?;
    }

    #[cfg(target_os = "macos")]
    {
        install_ollama_macos(window).await?;
    }

    #[cfg(not(any(windows, target_os = "macos")))]
    {
        return Err(OllamaInstallError::UnknownError(
            "지원하지 않는 운영체제입니다".to_string(),
        ));
    }

    emit_progress(
        window,
        OllamaInstallProgress::progress("completed", 100, "설치 완료"),
    )?;
    Ok(())
}

#[cfg(windows)]
async fn install_ollama_windows(window: &Window) -> Result<(), OllamaInstallError> {
    let installer_path = std::env::temp_dir().join("OllamaSetup.exe");
    download_file(window, WINDOWS_DOWNLOAD_URL, &installer_path).await?;
    run_install_command(
        window,
        tokio::process::Command::new(&installer_path).arg("/S"),
        "Ollama 설치 중...",
    )
    .await?;
    wait_for_ollama_executable(window).await
}

#[cfg(target_os = "macos")]
async fn install_ollama_macos(window: &Window) -> Result<(), OllamaInstallError> {
    if command_exists("brew") {
        emit_progress(
            window,
            OllamaInstallProgress::progress("installing", 50, "Homebrew로 설치 중..."),
        )?;
        return run_install_command(
            window,
            tokio::process::Command::new("brew")
                .arg("install")
                .arg("ollama"),
            "Homebrew로 설치 중...",
        )
        .await;
    }

    let archive_path = std::env::temp_dir().join("Ollama-darwin.zip");
    download_file(window, MACOS_DOWNLOAD_URL, &archive_path).await?;
    emit_progress(
        window,
        OllamaInstallProgress::progress("installing", 50, "압축 해제 및 설치 중..."),
    )?;
    run_install_command(
        window,
        tokio::process::Command::new("ditto")
            .arg("-x")
            .arg("-k")
            .arg(&archive_path)
            .arg("/Applications"),
        "압축 해제 및 설치 중...",
    )
    .await
}

async fn download_file(
    window: &Window,
    url: &str,
    destination: &Path,
) -> Result<(), OllamaInstallError> {
    let response = reqwest::get(url)
        .await
        .map_err(|error| OllamaInstallError::NetworkError(error.to_string()))?;

    if !response.status().is_success() {
        return Err(OllamaInstallError::NetworkError(format!(
            "서버 응답 오류: {}",
            response.status()
        )));
    }

    let total_size = response.content_length();
    let mut downloaded = 0_u64;
    let mut last_emit = Instant::now() - Duration::from_secs(1);
    let mut stream = response.bytes_stream();
    let mut file = tokio::fs::File::create(destination)
        .await
        .map_err(map_io_error)?;

    emit_progress(
        window,
        OllamaInstallProgress::progress("downloading", 0, "다운로드 중... 0%"),
    )?;

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| OllamaInstallError::NetworkError(error.to_string()))?;
        file.write_all(&chunk).await.map_err(map_io_error)?;
        downloaded += chunk.len() as u64;

        let download_percent = total_size
            .filter(|size| *size > 0)
            .map(|size| ((downloaded.saturating_mul(50) / size).min(50)) as u8)
            .unwrap_or(0);

        if last_emit.elapsed() >= Duration::from_secs(1) || download_percent == 50 {
            emit_progress(
                window,
                OllamaInstallProgress::progress(
                    "downloading",
                    download_percent,
                    format!("다운로드 중... {}%", download_percent.saturating_mul(2)),
                ),
            )?;
            last_emit = Instant::now();
        }
    }

    file.flush().await.map_err(map_io_error)?;
    emit_progress(
        window,
        OllamaInstallProgress::progress("installing", 50, "다운로드 완료, 설치 준비 중..."),
    )?;
    Ok(())
}

async fn run_install_command(
    window: &Window,
    command: &mut tokio::process::Command,
    message: &'static str,
) -> Result<(), OllamaInstallError> {
    let mut child = command.spawn().map_err(map_io_error)?;
    let mut interval = tokio::time::interval(Duration::from_secs(1));
    let mut progress = 50_u8;

    loop {
        tokio::select! {
            status = child.wait() => {
                let status = status.map_err(map_io_error)?;
                if status.success() {
                    emit_progress(window, OllamaInstallProgress::progress("installing", 99, "설치 마무리 중..."))?;
                    return Ok(());
                }
                return Err(OllamaInstallError::UnknownError(format!("설치 프로그램 종료 코드: {status}")));
            }
            _ = interval.tick() => {
                progress = progress.saturating_add(5).min(99);
                emit_progress(window, OllamaInstallProgress::progress("installing", progress, message))?;
            }
        }
    }
}

fn emit_progress(
    window: &Window,
    payload: OllamaInstallProgress,
) -> Result<(), OllamaInstallError> {
    window
        .emit(PROGRESS_EVENT, payload)
        .map_err(|error| OllamaInstallError::UnknownError(error.to_string()))
}

fn parse_installed_models(stdout: &str) -> Vec<String> {
    let installed: Vec<&str> = stdout
        .lines()
        .skip(1)
        .filter_map(|line| line.split_whitespace().next())
        .collect();

    REQUIRED_MODELS
        .iter()
        .filter(|model| installed.contains(model))
        .map(|model| (*model).to_string())
        .collect()
}

fn parse_ollama_progress(line: &str) -> Option<ProgressInfo> {
    let re = Regex::new(r"(\d+)%.*?(\d+\.?\d*)\s*([KMGT]?B)(?:\s*/\s*(\d+\.?\d*)\s*([KMGT]?B))?")
        .ok()?;
    let captures = re.captures(line)?;
    let percent = captures.get(1)?.as_str().parse().ok()?;
    let downloaded = format!("{}{}", captures.get(2)?.as_str(), captures.get(3)?.as_str());
    let total = match (captures.get(4), captures.get(5)) {
        (Some(size), Some(unit)) => format!("{}{}", size.as_str(), unit.as_str()),
        _ => downloaded.clone(),
    };

    Some(ProgressInfo {
        percent,
        downloaded,
        total,
    })
}

fn stream_pull_output<R>(mut reader: R, sender: tokio::sync::mpsc::UnboundedSender<String>)
where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut buffer = [0_u8; 1024];
        let mut pending = Vec::new();
        loop {
            let read = match reader.read(&mut buffer).await {
                Ok(read) => read,
                Err(_) => break,
            };
            if read == 0 {
                break;
            }

            for byte in &buffer[..read] {
                if *byte == b'\r' || *byte == b'\n' {
                    if !pending.is_empty() {
                        let line = String::from_utf8_lossy(&pending).to_string();
                        let _ = sender.send(line);
                        pending.clear();
                    }
                } else {
                    pending.push(*byte);
                }
            }
        }

        if !pending.is_empty() {
            let line = String::from_utf8_lossy(&pending).to_string();
            let _ = sender.send(line);
        }
    });
}

fn handle_model_pull_line(window: &Window, model_name: &str, line: &str) -> Result<(), String> {
    let normalized = line.replace('\r', "");

    if let Some(progress_info) = parse_ollama_progress(&normalized) {
        return emit_model_progress(
            window,
            ModelDownloadProgress {
                model: model_name.to_string(),
                status: "downloading",
                progress: progress_info.percent,
                downloaded: Some(progress_info.downloaded.clone()),
                total: Some(progress_info.total.clone()),
                message: format!(
                    "다운로드 중... {} / {}",
                    progress_info.downloaded, progress_info.total
                ),
            },
        );
    }

    if normalized.contains("pulling manifest") {
        return emit_model_progress(
            window,
            ModelDownloadProgress {
                model: model_name.to_string(),
                status: "pulling",
                progress: 0,
                downloaded: None,
                total: None,
                message: "manifest 받는 중...".to_string(),
            },
        );
    }

    if normalized.contains("verifying") {
        return emit_model_progress(
            window,
            ModelDownloadProgress {
                model: model_name.to_string(),
                status: "verifying",
                progress: 100,
                downloaded: None,
                total: None,
                message: "검증 중...".to_string(),
            },
        );
    }

    if normalized.contains("success") {
        return emit_model_progress(
            window,
            ModelDownloadProgress {
                model: model_name.to_string(),
                status: "completed",
                progress: 100,
                downloaded: None,
                total: None,
                message: "완료".to_string(),
            },
        );
    }

    Ok(())
}

fn emit_model_progress(window: &Window, payload: ModelDownloadProgress) -> Result<(), String> {
    window
        .emit(MODEL_PROGRESS_EVENT, payload)
        .map_err(|error| format!("진행 이벤트 전송 실패: {error}"))
}

async fn ensure_ollama_ready() -> Result<std::process::Output, String> {
    match run_ollama_list().await {
        Ok(output) if output.status.success() => return Ok(output),
        Ok(_) | Err(_) => {}
    }

    start_ollama_server().await?;

    let mut last_error = "Ollama가 실행되지 않았습니다".to_string();
    for _ in 0..30 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        match run_ollama_list().await {
            Ok(output) if output.status.success() => return Ok(output),
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr);
                last_error = map_ollama_error_message(&stderr, "Ollama 준비 대기 중");
            }
            Err(error) => last_error = error,
        }
    }

    Err(last_error)
}

async fn run_ollama_list() -> Result<std::process::Output, String> {
    let ollama = ollama_executable_for_command();
    tokio::process::Command::new(&ollama)
        .arg("list")
        .output()
        .await
        .map_err(|_| "Ollama 실행 파일을 찾을 수 없습니다".to_string())
}

async fn start_ollama_server() -> Result<(), String> {
    let ollama = ollama_executable_for_command();
    tokio::process::Command::new(&ollama)
        .arg("serve")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|_| "Ollama 서버를 시작할 수 없습니다".to_string())
}

fn ollama_executable_for_command() -> PathBuf {
    resolve_ollama_executable().unwrap_or_else(|| PathBuf::from("ollama"))
}

fn resolve_ollama_executable() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        let localappdata = std::env::var("LOCALAPPDATA").ok();
        let userprofile = std::env::var("USERPROFILE").ok();
        if let Some(path) = windows_ollama_path_candidates(localappdata.as_deref(), userprofile.as_deref())
            .into_iter()
            .find(|path| path.exists())
        {
            return Some(path);
        }
    }

    command_exists("ollama").then(|| PathBuf::from("ollama"))
}

#[cfg(windows)]
async fn wait_for_ollama_executable(window: &Window) -> Result<(), OllamaInstallError> {
    for _ in 0..60 {
        if resolve_ollama_executable().is_some() {
            emit_progress(
                window,
                OllamaInstallProgress::progress("installing", 99, "Ollama 실행 파일 확인 완료"),
            )?;
            return Ok(());
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }

    Err(OllamaInstallError::UnknownError(
        "설치 후 Ollama 실행 파일을 찾을 수 없습니다".to_string(),
    ))
}

fn map_ollama_error_message(stderr: &str, default_message: &str) -> String {
    let lower = stderr.to_lowercase();
    if lower.contains("no space") || lower.contains("not enough space") {
        "디스크 공간 부족".to_string()
    } else if lower.contains("network")
        || lower.contains("connection")
        || lower.contains("timeout")
        || lower.contains("timed out")
    {
        "다운로드 실패: 네트워크 확인".to_string()
    } else if stderr.trim().is_empty() {
        default_message.to_string()
    } else {
        format!("{}: {}", default_message, stderr.trim())
    }
}

#[cfg(any(windows, test))]
fn windows_ollama_path_candidates(localappdata: Option<&str>, userprofile: Option<&str>) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if let Some(localappdata) = localappdata.filter(|value| !value.trim().is_empty()) {
        push_unique_path(
            &mut paths,
            PathBuf::from(localappdata)
                .join("Programs")
                .join("Ollama")
                .join("ollama.exe"),
        );
    }
    if let Some(userprofile) = userprofile.filter(|value| !value.trim().is_empty()) {
        let userprofile = PathBuf::from(userprofile);
        push_unique_path(
            &mut paths,
            userprofile
                .join("AppData")
                .join("Local")
                .join("Programs")
                .join("Ollama")
                .join("ollama.exe"),
        );
        push_unique_path(&mut paths, userprofile.join(".ollama").join("ollama.exe"));
    }
    paths
}

#[cfg(any(windows, test))]
fn push_unique_path(paths: &mut Vec<PathBuf>, path: PathBuf) {
    if !paths.iter().any(|existing| existing == &path) {
        paths.push(path);
    }
}

fn command_exists(command: &str) -> bool {
    #[cfg(windows)]
    let checker = "where";
    #[cfg(not(windows))]
    let checker = "which";

    Command::new(checker)
        .arg(command)
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn map_io_error(error: std::io::Error) -> OllamaInstallError {
    if error.kind() == std::io::ErrorKind::PermissionDenied {
        return OllamaInstallError::PermissionError("관리자 권한이 필요합니다".to_string());
    }

    if is_disk_space_error(&error) {
        return OllamaInstallError::DiskSpaceError(
            "사용 가능한 저장 공간이 부족합니다".to_string(),
        );
    }

    OllamaInstallError::UnknownError(error.to_string())
}

fn is_disk_space_error(error: &std::io::Error) -> bool {
    matches!(error.raw_os_error(), Some(28) | Some(112))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_installed_models_skips_header_and_reads_names() {
        let stdout = "NAME              ID          SIZE    MODIFIED\n\
bge-m3:latest     abc123      600 MB  2 days ago\n\
qwen2.5:7b        def456      4.7 GB  1 day ago\n";

        assert_eq!(
            parse_installed_models(stdout),
            vec!["bge-m3:latest".to_string(), "qwen2.5:7b".to_string()]
        );
    }

    #[test]
    fn parse_installed_models_only_returns_required_models() {
        let stdout = "NAME              ID          SIZE    MODIFIED\n\
qwen2.5:32b      abc123      19 GB   2 days ago\n\
bge-m3:latest    def456      1.2 GB  1 day ago\n";

        assert_eq!(
            parse_installed_models(stdout),
            vec!["bge-m3:latest".to_string()]
        );
    }

    #[test]
    fn parse_ollama_progress_reads_partial_download_line() {
        let info =
            parse_ollama_progress("pulling 8c17c2940df...  45% ▕███████▌        ▏ 2.1 GB / 4.7 GB")
                .expect("progress line should parse");

        assert_eq!(info.percent, 45);
        assert_eq!(info.downloaded, "2.1GB");
        assert_eq!(info.total, "4.7GB");
    }

    #[test]
    fn parse_ollama_progress_uses_downloaded_as_total_when_only_one_size_exists() {
        let info = parse_ollama_progress("pulling 8c17c2940df... 100% ▕████████████████▏ 4.7 GB")
            .expect("completed layer line should parse");

        assert_eq!(info.percent, 100);
        assert_eq!(info.downloaded, "4.7GB");
        assert_eq!(info.total, "4.7GB");
    }

    #[test]
    fn windows_ollama_path_candidates_include_real_installer_locations_before_legacy_path() {
        let paths = windows_ollama_path_candidates(
            Some("D:\\OllamaLocal"),
            Some("C:\\Users\\tester"),
        );

        assert_eq!(
            paths,
            vec![
                std::path::PathBuf::from("D:\\OllamaLocal")
                    .join("Programs")
                    .join("Ollama")
                    .join("ollama.exe"),
                std::path::PathBuf::from("C:\\Users\\tester")
                    .join("AppData")
                    .join("Local")
                    .join("Programs")
                    .join("Ollama")
                    .join("ollama.exe"),
                std::path::PathBuf::from("C:\\Users\\tester")
                    .join(".ollama")
                    .join("ollama.exe"),
            ]
        );
    }

    #[test]
    fn windows_ollama_path_candidates_deduplicate_localappdata_and_userprofile_paths() {
        let paths = windows_ollama_path_candidates(
            Some("C:\\Users\\tester\\AppData\\Local"),
            Some("C:\\Users\\tester"),
        );

        let unique: std::collections::HashSet<_> = paths.iter().collect();
        assert_eq!(unique.len(), paths.len());
    }

    #[test]
    fn install_error_payload_contains_korean_message() {
        let error = OllamaInstallError::NetworkError("연결 실패".to_string());
        let payload = error.payload();

        assert_eq!(payload.status, "error");
        assert_eq!(payload.error.as_deref(), Some("NetworkError"));
        assert!(payload.message.contains("다운로드 실패"));
    }
}
