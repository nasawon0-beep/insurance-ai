//! Windows firewall configuration is not a phone connectivity test.
//! Only explicit, non-QA confirmation reaches UAC. Queries never elevate/write.
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

const POLICY: &str = include_str!("mobile_firewall.ps1");

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct FirewallStatus {
    pub supported: bool,
    pub qa: bool,
    pub network_category: String,
    pub rule_state: String,
    pub rule_ready: bool,
    pub can_apply: bool,
    pub program_path: Option<String>,
    pub rule_name: Option<String>,
    pub connectivity_verified: bool,
    pub last_error: Option<String>,
    pub apply_outcome: Option<String>,
}

impl FirewallStatus {
    fn unavailable(qa: bool, error: String) -> Self {
        Self {
            supported: cfg!(windows),
            qa,
            network_category: "unavailable".into(),
            rule_state: "unknown".into(),
            rule_ready: false,
            can_apply: false,
            program_path: None,
            rule_name: None,
            connectivity_verified: false,
            last_error: Some(error),
            apply_outcome: None,
        }
    }
}

fn mutation_gate(qa: bool, confirmed: bool) -> Result<(), &'static str> {
    if qa {
        return Err("qaRejected");
    }
    if !confirmed {
        return Err("notConfirmed");
    }
    Ok(())
}

fn ps_literal(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

// Windows PowerShell -EncodedCommand requires Base64 of UTF-16LE, not UTF-8.
fn encoded_command(script: &str) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let bytes: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut output = String::new();
    for chunk in bytes.chunks(3) {
        let a = chunk[0];
        let b = *chunk.get(1).unwrap_or(&0);
        let c = *chunk.get(2).unwrap_or(&0);
        output.push(ALPHABET[(a >> 2) as usize] as char);
        output.push(ALPHABET[(((a & 3) << 4) | (b >> 4)) as usize] as char);
        output.push(if chunk.len() > 1 {
            ALPHABET[(((b & 15) << 2) | (c >> 6)) as usize] as char
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            ALPHABET[(c & 63) as usize] as char
        } else {
            '='
        });
    }
    output
}

fn policy_script(program: &Path, host: Option<&str>, qa: bool, operation: &str) -> String {
    // All variable inputs are literal data. No writable temporary script is elevated.
    format!(
        "$env:PSModulePath = Join-Path $PSHOME 'Modules'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & {{\n{POLICY}\n}} -Program {} -HostAddress {} -Qa ${} -Operation {}",
        ps_literal(&program.to_string_lossy()), ps_literal(host.unwrap_or("")),
        if qa { "true" } else { "false" }, ps_literal(operation)
    )
}

#[cfg(windows)]
fn powershell_path() -> Result<PathBuf, String> {
    // Do not search PATH or trust an inherited SystemRoot for an elevated executable.
    #[link(name = "kernel32")]
    extern "system" {
        fn GetSystemDirectoryW(buffer: *mut u16, size: u32) -> u32;
    }
    let mut buffer = vec![0u16; 32768];
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    if length == 0 || length >= buffer.len() {
        return Err("Windows system directory unavailable".into());
    }
    Ok(PathBuf::from(
        String::from_utf16(&buffer[..length]).map_err(|_| "Invalid system directory")?,
    )
    .join("WindowsPowerShell")
    .join("v1.0")
    .join("powershell.exe"))
}

#[cfg(not(windows))]
fn powershell_path() -> Result<PathBuf, String> {
    Err("Windows firewall is not supported on this platform".into())
}

fn run_script(script: &str) -> Result<std::process::Output, String> {
    let mut command = std::process::Command::new(powershell_path()?);
    command.args([
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW (UAC retains its own consent UI)
    }
    command
        .output()
        .map_err(|e| format!("Firewall query/UAC launch failed: {e}"))
}

fn installed_program() -> Result<PathBuf, String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let program = executable
        .parent()
        .ok_or("App installation directory unavailable")?
        .join("local-engine.exe");
    if !program.is_file() {
        return Err("Installed local-engine.exe was not found beside the running app".into());
    }
    // Preserve normal Win32 path syntax (canonicalize would introduce a \\?\ prefix).
    Ok(program)
}

fn query_at(program: &Path, host: Option<&str>, qa: bool) -> FirewallStatus {
    let result = run_script(&policy_script(program, host, qa, "query")).and_then(|output| {
        if !output.status.success() {
            return Err("Windows firewall query failed".into());
        }
        serde_json::from_slice::<FirewallStatus>(&output.stdout)
            .map_err(|_| "Windows firewall response unavailable".into())
    });
    result.unwrap_or_else(|e| FirewallStatus::unavailable(qa, e))
}

pub(crate) fn status(qa: bool, host: Option<&str>) -> FirewallStatus {
    match installed_program() {
        Ok(program) => query_at(&program, host, qa),
        Err(e) => FirewallStatus::unavailable(qa, e),
    }
}

fn elevation_script(executable: &Path, payload: &str) -> String {
    // Start-Process flattens ArgumentList. Only fixed switches and a Base64 token
    // cross that boundary; paths/spaces/quotes remain inside the encoded payload.
    format!("$ErrorActionPreference='Stop'; try {{ $p=Start-Process -FilePath {} -Verb RunAs -Wait -PassThru -ArgumentList '-NoLogo -NoProfile -NonInteractive -EncodedCommand {}'; exit $p.ExitCode }} catch {{ exit 1 }}", ps_literal(&executable.to_string_lossy()), encoded_command(payload))
}

pub(crate) fn apply(qa: bool, confirmed: bool, enable: bool, host: Option<&str>) -> FirewallStatus {
    // No UAC or script execution before both gates pass. Rejection still returns a read-only snapshot.
    if let Err(reason) = mutation_gate(qa, confirmed) {
        let mut snapshot = status(qa, host);
        snapshot.apply_outcome = Some(reason.into());
        snapshot.last_error = Some(
            if qa {
                "QA profiles cannot change firewall rules"
            } else {
                "Explicit user confirmation is required"
            }
            .into(),
        );
        return snapshot;
    }
    let program = match installed_program() {
        Ok(program) => program,
        Err(e) => {
            let mut s = FirewallStatus::unavailable(qa, e);
            s.apply_outcome = Some("failed".into());
            return s;
        }
    };
    let before = query_at(&program, host, qa);
    if before.last_error.is_some() || (enable && !before.can_apply) {
        let mut snapshot = before;
        snapshot.apply_outcome = Some("blocked".into());
        if snapshot.last_error.is_none() {
            snapshot.last_error = Some(
                "A verified Private network is required; network categories are never changed"
                    .into(),
            );
        }
        return snapshot;
    }
    let result = powershell_path().and_then(|exe| {
        run_script(&elevation_script(
            &exe,
            &policy_script(
                &program,
                host,
                qa,
                if enable { "enable" } else { "disable" },
            ),
        ))
    });
    // Always reread, including launch failure/UAC denial/partial application.
    let mut after = query_at(&program, host, qa);
    let success = result.as_ref().map(|o| o.status.success()).unwrap_or(false)
        && after.last_error.is_none()
        && if enable {
            after.rule_ready
        } else {
            matches!(after.rule_state.as_str(), "disabled" | "missing")
        };
    after.apply_outcome = Some(if success { "applied" } else { "failed" }.into());
    if !success {
        after.last_error = Some("Firewall change was cancelled, failed, or could not be verified. No connectivity claim is made; refresh status before retrying.".into());
    }
    after
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn firewall_rule_is_private_program_and_port_scoped() {
        assert!(POLICY.contains("Profile='Private'"));
        assert!(POLICY.contains("Protocol='TCP'; LocalPort=18443"));
        assert!(POLICY.contains("LocalAddress='Any'; RemoteAddress='LocalSubnet'"));
        assert!(!POLICY.contains("Set-NetConnectionProfile"));
        assert!(!POLICY.contains("Remove-NetFirewallRule"));
        assert!(!POLICY.contains("5353")); // QR uses direct TLS; discovery stays best effort.
    }
    #[test]
    fn firewall_confirmation_and_qa_gates_fail_closed() {
        assert_eq!(mutation_gate(true, true), Err("qaRejected"));
        assert_eq!(mutation_gate(true, false), Err("qaRejected"));
        assert_eq!(mutation_gate(false, false), Err("notConfirmed"));
        assert_eq!(mutation_gate(false, true), Ok(()));
    }
    #[test]
    fn firewall_powershell_encoding_is_utf16le_and_data_is_literal() {
        assert_eq!(encoded_command("A"), "QQA=");
        assert_eq!(encoded_command("AB"), "QQBCAA==");
        assert_eq!(encoded_command("ABC"), "QQBCAEMA");
        assert_eq!(encoded_command(""), "");
        assert_eq!(
            ps_literal("C:\\Moa'Doom\\$(bad).exe"),
            "'C:\\Moa''Doom\\$(bad).exe'"
        );
        let script = policy_script(
            Path::new("C:\\Program Files\\Moa'Doom\\local-engine.exe"),
            None,
            false,
            "enable",
        );
        assert!(script.contains("-Program 'C:\\Program Files\\Moa''Doom\\local-engine.exe'"));
        let wrapper = elevation_script(Path::new("C:\\Windows\\powershell.exe"), &script);
        assert!(!wrapper.contains("Moa'Doom"));
        assert!(!wrapper.contains("-Command "));
        assert!(wrapper.contains("-EncodedCommand "));
    }
    // Conservative Windows CRT quoting bound: quote every argument, including
    // those Command can leave unquoted. Include doubled trailing backslashes,
    // escaped double quotes, UTF-16 surrogate pairs, and the final NUL.
    fn windows_command_units(executable: &str, args: &[&str]) -> usize {
        fn quoted_units(value: &str) -> usize {
            let mut count = 2;
            let mut slashes = 0;
            for unit in value.encode_utf16() {
                if unit == b'\\' as u16 {
                    slashes += 1;
                } else {
                    count += slashes + 1;
                    if unit == b'"' as u16 {
                        count += slashes + 1;
                    }
                    slashes = 0;
                }
            }
            count + slashes * 2
        }
        quoted_units(executable) + args.iter().map(|arg| 1 + quoted_units(arg)).sum::<usize>() + 1
    }

    #[test]
    fn firewall_nested_uac_command_fits_windows_limit_with_quoting() {
        let executable = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";
        // Exercise a long installation path, Unicode, spaces, and PS metacharacters.
        let program = format!(
            "C:\\{}\\Moa'Doom 한글 $()\\local-engine.exe",
            "directory ".repeat(20)
        );
        for operation in ["query", "enable", "disable"] {
            let payload = policy_script(
                Path::new(&program),
                Some("192.168.255.254"),
                false,
                operation,
            );
            let wrapper = elevation_script(Path::new(executable), &payload);
            let query_units = windows_command_units(
                executable,
                &[
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    &payload,
                ],
            );
            let outer_units = windows_command_units(
                executable,
                &[
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    &wrapper,
                ],
            );
            let encoded = encoded_command(&payload);
            let inner_units = windows_command_units(
                executable,
                &[
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    &encoded,
                ],
            );
            println!("{operation}: query={query_units}, outer={outer_units}, inner={inner_units} UTF-16 units including quoting/NUL");
            assert!(query_units <= 32767 && outer_units <= 32767 && inner_units <= 32767);
            // Guard the original regression: encoding the already encoded wrapper is too long.
            let double_encoded = encoded_command(&wrapper);
            assert!(
                windows_command_units(executable, &["-EncodedCommand", &double_encoded]) > 32767
            );
        }
        assert_eq!(windows_command_units("x", &["a\\\"b", "z\\"]), 19);
    }

    #[test]
    fn firewall_commands_offload_blocking_process_work() {
        let source = include_str!("mobile_sync.rs");
        for name in ["mobile_sync_firewall_status", "mobile_sync_firewall_apply"] {
            let body = source
                .split(&format!("pub(crate) async fn {name}("))
                .nth(1)
                .expect("async firewall command");
            let body = body.split("\n}\n").next().unwrap();
            assert!(body.contains("tauri::async_runtime::spawn_blocking(move ||"));
            assert!(body.contains(".await"));
        }
    }

    #[test]
    fn firewall_query_contract_never_claims_connectivity() {
        let s = FirewallStatus::unavailable(false, "test".into());
        let value = serde_json::to_value(s).unwrap();
        assert_eq!(value["networkCategory"], "unavailable");
        assert_eq!(value["connectivityVerified"], false);
        assert_eq!(value["ruleReady"], false);
        assert_eq!(value["canApply"], false);
        assert!(value.get("programPath").is_some());
        assert!(value.get("applyOutcome").is_some());
    }
    #[test]
    fn firewall_policy_uses_literal_installation_ownership_and_effective_readback() {
        assert!(POLICY.contains("SHA256"));
        assert!(POLICY.contains("$_.Name -ceq $name"));
        assert!(POLICY.contains("$Rule.Group -ceq $owner -and $Rule.Description -ceq $description"));
        assert!(POLICY.contains("elseif (-not (ExactRule $rules[0]))"));
        assert!(POLICY.contains("Set-NetFirewallRule -InputObject $rules[0] @settings"));
        assert!(POLICY.contains("-PolicyStore ActiveStore"));
    }
}
