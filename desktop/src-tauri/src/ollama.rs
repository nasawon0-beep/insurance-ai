use futures_util::StreamExt;
use serde::Serialize;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};
use tauri::{Emitter, Window};
use tokio::io::AsyncWriteExt;

#[cfg(windows)]
const WINDOWS_DOWNLOAD_URL: &str =
    "https://github.com/ollama/ollama/releases/download/v0.1.29/OllamaSetup.exe";
#[cfg(target_os = "macos")]
const MACOS_DOWNLOAD_URL: &str =
    "https://github.com/ollama/ollama/releases/download/v0.1.29/Ollama-darwin.zip";
const PROGRESS_EVENT: &str = "ollama-install-progress";

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
        return Ok(std::env::var("USERPROFILE")
            .map(|userprofile| windows_ollama_path_from_userprofile(&userprofile).exists())
            .unwrap_or(false));
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
    .await
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

#[cfg(any(windows, test))]
fn windows_ollama_path_from_userprofile(userprofile: &str) -> std::path::PathBuf {
    std::path::PathBuf::from(userprofile)
        .join(".ollama")
        .join("ollama.exe")
}

#[cfg(target_os = "macos")]
fn command_exists(command: &str) -> bool {
    Command::new("which")
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
    fn windows_ollama_path_uses_userprofile() {
        let path = windows_ollama_path_from_userprofile("C:\\Users\\tester");

        assert_eq!(
            path.file_name().and_then(|name| name.to_str()),
            Some("ollama.exe")
        );
        assert_eq!(
            path.parent()
                .and_then(|parent| parent.file_name())
                .and_then(|name| name.to_str()),
            Some(".ollama")
        );
        assert!(path.starts_with("C:\\Users\\tester"));
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
