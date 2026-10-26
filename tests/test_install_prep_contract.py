"""Installation preparation contracts; no sidecar or installer is built here."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _app_versions():
    package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))["version"]
    package_lock = json.loads((ROOT / "desktop/package-lock.json").read_text(encoding="utf-8"))
    assert package_lock["version"] == package_lock["packages"][""]["version"]
    tauri = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    cargo_toml = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    cargo_lock = (ROOT / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    cargo_match = re.search(r"^\[package\]\s+[\s\S]*?^version\s*=\s*\"([^\"]+)\"", cargo_toml, re.M)
    lock_match = re.search(r'^name = "desktop-scaffold"\r?\nversion = "([^"]+)"', cargo_lock, re.M)
    assert cargo_match is not None and lock_match is not None
    cargo = cargo_match.group(1)
    lock = lock_match.group(1)
    return package, package_lock["version"], tauri, cargo, lock


def test_app_versions_match_desktop_package_source_of_truth():
    source_version = json.loads(
        (ROOT / "desktop/package.json").read_text(encoding="utf-8")
    )["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", source_version)
    assert _app_versions() == (source_version,) * 5


def test_sync_version_handles_windows_cargo_lock_newlines_and_is_strict():
    source = (ROOT / "scripts/sync-version.mjs").read_text(encoding="utf-8")
    assert r'name = "desktop-scaffold"\r?\nversion = "' in source
    assert "matches.length !== 1" in source
    assert "desktop/package.json" in source
    assert "desktop/package-lock.json" in source


def test_windows_sidecar_build_is_pinned_to_python_311():
    source = (ROOT / "scripts/build-sidecars.ps1").read_text(encoding="utf-8")
    assert 'Programs\\Python\\Python311\\python.exe' in source
    assert '$BuildScratch = Join-Path $Root ".build-tmp"' in source
    assert '$env:TEMP = Join-Path $BuildScratch "temp"' in source
    assert '$env:TMP = $env:TEMP' in source
    assert '$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildScratch "pyinstaller"' in source
    assert ".build-tmp/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert '[System.IO.Path]::GetTempPath()' not in source
    assert '$PythonVersion -ne "3.11"' in source
    assert "& $Python311 -m venv $ControlVenvDir" in source
    assert "& $Python311 -m venv $LocalEngineVenvDir" in source
    assert "print(sys.version_info.major,sys.version_info.minor,sep=chr(46))" in source
    assert "PyInstaller.__version__.split(chr(46))[0] == chr(54)" in source
    assert "print(f" not in source
    assert "$ProbeOutput = & $PythonPath -c" in source
    assert "if ($LASTEXITCODE -ne 0)" in source
    assert "([string]$ProbeOutput).Trim()" in source
    assert "-m PyInstaller --clean --noconfirm control-server.spec" in source
    assert "-m PyInstaller --clean --noconfirm local-engine.spec" in source
    assert '"control-server"' in source and '"local-engine"' in source


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell/Python installation contract")
def test_powershell_51_native_argv_probes_three_interpreters(tmp_path):
    python311 = Path(os.environ["LOCALAPPDATA"]) / "Programs/Python/Python311/python.exe"
    assert python311.is_file(), python311
    control_venv = tmp_path / "control-server-venv"
    local_venv = tmp_path / "local-engine-venv"
    for venv in (control_venv, local_venv):
        created = subprocess.run(
            [str(python311), "-m", "venv", "--without-pip", str(venv)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert created.returncode == 0, created.stderr or created.stdout
    interpreters = [
        python311,
        control_venv / "Scripts/python.exe",
        local_venv / "Scripts/python.exe",
    ]
    assert all(path.is_file() for path in interpreters), interpreters

    probe = tmp_path / "probe-python-argv.ps1"
    probe.write_text(
        "param([string]$BasePython,[string]$ControlPython,[string]$LocalPython)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Write-Output (\"POWERSHELL_OK|\" + $PSVersionTable.PSVersion.ToString())\n"
        "$PythonPaths = @($BasePython,$ControlPython,$LocalPython)\n"
        "foreach ($PythonPath in $PythonPaths) {\n"
        "  $Output = & $PythonPath -c 'import sys; print(sys.version_info.major,sys.version_info.minor,sep=chr(46))'\n"
        "  if ($LASTEXITCODE -ne 0) { throw \"version probe failed: $PythonPath exit=$LASTEXITCODE\" }\n"
        "  $Version = ([string]$Output).Trim()\n"
        "  if ($Version -ne '3.11') { throw \"wrong version: $PythonPath=$Version\" }\n"
        "  Write-Output \"VERSION_OK|$PythonPath|$Version\"\n"
        "}\n"
        "$PyInstallerPython = $PythonPaths[0]\n"
        "& $PyInstallerPython -c 'import PyInstaller,sys; sys.exit(0 if PyInstaller.__version__.split(chr(46))[0] == chr(54) else 1)'\n"
        "if ($LASTEXITCODE -ne 0) { throw \"PyInstaller major probe failed: exit=$LASTEXITCODE\" }\n"
        "Write-Output 'PYINSTALLER_MAJOR_OK|6'\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
            *map(str, interpreters),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="cp949",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    print(result.stdout, end="")
    assert "POWERSHELL_OK|5.1" in result.stdout
    assert result.stdout.count("VERSION_OK|") == 3
    assert "PYINSTALLER_MAJOR_OK|6" in result.stdout


def test_llama_cpp_uses_pinned_official_cpu_wheel_index():
    requirements = (ROOT / "local-engine/requirements.txt").read_text(encoding="ascii")
    assert "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu" in requirements
    assert "--only-binary=llama-cpp-python" in requirements
    assert "llama-cpp-python==0.3.23" in requirements
    assert "llama-cpp-python>=" not in requirements


def test_tauri_external_bins_and_updater_artifacts_are_declared():
    tauri = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    bundle = tauri["bundle"]
    assert bundle["externalBin"] == ["binaries/control-server", "binaries/local-engine"]
    assert bundle["createUpdaterArtifacts"] is True


def test_pyinstaller_specs_cover_required_runtime_data():
    local_spec = (ROOT / "local-engine/local-engine.spec").read_text(encoding="utf-8")
    control_spec = (ROOT / "control-server/control-server.spec").read_text(encoding="utf-8")
    for resource in (
        "ocr/models",
        "database/coverage-rag-queries.json",
        "database/coverage-recommended-amounts.json",
        "database/import_data.py",
    ):
        assert resource in local_spec
    assert 'name="local-engine"' in local_spec
    assert 'name="control-server"' in control_spec
    assert "update_manifest.json" in control_spec
