$ErrorActionPreference = "Stop"
# UTF-8 모드: pip 가 requirements.txt 를 cp1252 로 읽어 깨지는 것 방지
$env:PYTHONUTF8 = "1"

$Root = Split-Path -Parent $PSScriptRoot
$ControlDir = Join-Path $Root "control-server"
$LocalEngineDir = Join-Path $Root "local-engine"
$ControlPython = Join-Path $ControlDir ".venv\Scripts\python.exe"
$LocalEnginePython = Join-Path $LocalEngineDir ".venv\Scripts\python.exe"
$DestDir = Join-Path $Root "desktop\src-tauri\binaries"
$TargetTriple = (rustc -Vv | Select-String '^host: ').Line -replace '^host: ', ''
$env:PYINSTALLER_CONFIG_DIR = Join-Path ([System.IO.Path]::GetTempPath()) "insurance-ai-pyinstaller"
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

if (-not $TargetTriple) {
    throw "rustc host target triple을 확인할 수 없습니다."
}

if (-not (Test-Path $ControlPython)) {
    python -m venv (Join-Path $ControlDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "control-server venv 생성 실패" }
}
& $ControlPython -m pip install -r (Join-Path $ControlDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "control-server 의존성 설치 실패" }
& $ControlPython -c 'import PyInstaller, sys; sys.exit(0 if PyInstaller.__version__.split(".")[0] == "6" else 1)'
if ($LASTEXITCODE -ne 0) {
    & $ControlPython -m pip install 'pyinstaller>=6,<7'
    if ($LASTEXITCODE -ne 0) { throw "control-server PyInstaller 설치 실패" }
}
Push-Location $ControlDir
try {
    & $ControlPython -m PyInstaller --clean --noconfirm control-server.spec
    if ($LASTEXITCODE -ne 0) { throw "control-server 빌드 실패" }
} finally {
    Pop-Location
}

if (-not (Test-Path $LocalEnginePython)) {
    python -m venv (Join-Path $LocalEngineDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "local-engine venv 생성 실패" }
}
& $LocalEnginePython -m pip install -r (Join-Path $LocalEngineDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "local-engine 의존성 설치 실패" }
& $LocalEnginePython -c 'import PyInstaller, sys; sys.exit(0 if PyInstaller.__version__.split(".")[0] == "6" else 1)'
if ($LASTEXITCODE -ne 0) {
    & $LocalEnginePython -m pip install 'pyinstaller>=6,<7'
    if ($LASTEXITCODE -ne 0) { throw "local-engine PyInstaller 설치 실패" }
}
Push-Location $LocalEngineDir
try {
    & $LocalEnginePython -m PyInstaller --clean --noconfirm local-engine.spec
    if ($LASTEXITCODE -ne 0) { throw "local-engine 빌드 실패" }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

function Install-Sidecar([string]$Source, [string]$Name) {
    $Destination = Join-Path $DestDir "$Name-$TargetTriple.exe"
    Remove-Item -Force -ErrorAction SilentlyContinue $Destination
    Copy-Item $Source $Destination
    Write-Host "sidecar: $Destination"
}

Install-Sidecar (Join-Path $ControlDir "dist\control-server.exe") "control-server"
Install-Sidecar (Join-Path $LocalEngineDir "dist\local-engine.exe") "local-engine"
