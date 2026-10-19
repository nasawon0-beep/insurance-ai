$ErrorActionPreference = "Stop"
# UTF-8 모드: pip 가 requirements.txt 를 cp1252 로 읽어 깨지는 것 방지
$env:PYTHONUTF8 = "1"

$Root = Split-Path -Parent $PSScriptRoot
$ControlDir = Join-Path $Root "control-server"
$LocalEngineDir = Join-Path $Root "local-engine"
$Python311 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
$ControlVenvDir = Join-Path $ControlDir ".venv"
$LocalEngineVenvDir = Join-Path $LocalEngineDir ".venv"
$ControlPython = Join-Path $ControlVenvDir "Scripts\python.exe"
$LocalEnginePython = Join-Path $LocalEngineVenvDir "Scripts\python.exe"
$DestDir = Join-Path $Root "desktop\src-tauri\binaries"
$BuildScratch = Join-Path $Root ".build-tmp"
$env:TEMP = Join-Path $BuildScratch "temp"
$env:TMP = $env:TEMP
$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildScratch "pyinstaller"
$TargetTriple = (rustc -Vv | Select-String '^host: ').Line -replace '^host: ', ''
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

function Get-PythonMajorMinor([string]$PythonPath, [string]$Label) {
    $ProbeOutput = & $PythonPath -c 'import sys; print(sys.version_info.major,sys.version_info.minor,sep=chr(46))'
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 버전 확인 명령이 실패했습니다 (exit=$LASTEXITCODE)"
    }
    return ([string]$ProbeOutput).Trim()
}

function Test-PyInstallerMajor6([string]$PythonPath) {
    & $PythonPath -c 'import PyInstaller,sys; sys.exit(0 if PyInstaller.__version__.split(chr(46))[0] == chr(54) else 1)'
    return $LASTEXITCODE -eq 0
}

if (-not $TargetTriple) {
    throw "rustc host target triple을 확인할 수 없습니다."
}
if (-not (Test-Path $Python311)) {
    throw "Python 3.11 실행 파일이 없습니다: $Python311"
}
$PythonVersion = Get-PythonMajorMinor $Python311 "기본 Python"
if ($PythonVersion -ne "3.11") {
    throw "sidecar 빌드는 Python 3.11이 필요합니다 (확인값: $PythonVersion)"
}

if (-not (Test-Path $ControlPython)) {
    & $Python311 -m venv $ControlVenvDir
    if ($LASTEXITCODE -ne 0) { throw "control-server Python 3.11 venv 생성 실패" }
}
$ControlVenvVersion = Get-PythonMajorMinor $ControlPython "control-server .venv"
if ($ControlVenvVersion -ne "3.11") {
    throw "control-server .venv가 Python 3.11이 아닙니다 (확인값: $ControlVenvVersion). .venv를 수동 정리 후 재실행하세요."
}
& $ControlPython -m pip install -r (Join-Path $ControlDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "control-server 의존성 설치 실패" }
if (-not (Test-PyInstallerMajor6 $ControlPython)) {
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
    & $Python311 -m venv $LocalEngineVenvDir
    if ($LASTEXITCODE -ne 0) { throw "local-engine Python 3.11 venv 생성 실패" }
}
$LocalEngineVenvVersion = Get-PythonMajorMinor $LocalEnginePython "local-engine .venv"
if ($LocalEngineVenvVersion -ne "3.11") {
    throw "local-engine .venv가 Python 3.11이 아닙니다 (확인값: $LocalEngineVenvVersion). .venv를 수동 정리 후 재실행하세요."
}
& $LocalEnginePython -m pip install -r (Join-Path $LocalEngineDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "local-engine 의존성 설치 실패" }
if (-not (Test-PyInstallerMajor6 $LocalEnginePython)) {
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
