$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$Root = Split-Path -Parent $PSScriptRoot
$LocalEngineDir = Join-Path $Root "local-engine"
$Python311 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
$LocalEngineVenvPython = Join-Path $LocalEngineDir ".venv\Scripts\python.exe"
$LocalEnginePython = if (Test-Path $LocalEngineVenvPython) { $LocalEngineVenvPython } else { $Python311 }
$BuildRoot = Join-Path $Root ".build-tmp\local-engine-onedir"
$WorkPath = Join-Path $BuildRoot "work"
$DistPath = Join-Path $BuildRoot "dist"
$StageDir = Join-Path $Root "desktop\src-tauri\binaries\onedir"
$TargetTriple = (rustc -Vv | Select-String '^host: ').Line -replace '^host: ', ''

if (-not $TargetTriple) {
    throw "rustc host target triple을 확인할 수 없습니다."
}
if (-not (Test-Path $LocalEnginePython)) {
    throw "기존 Python 3.11/PyInstaller 환경이 없습니다: $LocalEnginePython"
}

$PythonVersion = (& $LocalEnginePython -c 'import sys; print(sys.version_info.major,sys.version_info.minor,sep=chr(46))').Trim()
if ($LASTEXITCODE -ne 0 -or $PythonVersion -ne "3.11") {
    throw "local-engine onedir 빌드는 기존 Python 3.11 venv가 필요합니다 (확인값: $PythonVersion)"
}
& $LocalEnginePython -c 'import PyInstaller,sys; sys.exit(0 if PyInstaller.__version__.split(chr(46))[0] == chr(54) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw "기존 local-engine venv의 PyInstaller major 6이 필요합니다"
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $BuildRoot
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $StageDir
New-Item -ItemType Directory -Force -Path $WorkPath, $DistPath, $StageDir | Out-Null

$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
try {
    $env:TEMP = Join-Path $BuildRoot "temp"
    $env:TMP = $env:TEMP
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot "pyinstaller"
    New-Item -ItemType Directory -Force -Path $env:TEMP, $env:PYINSTALLER_CONFIG_DIR | Out-Null

    Push-Location $LocalEngineDir
    try {
        & $LocalEnginePython -m PyInstaller --clean --noconfirm --workpath $WorkPath --distpath $DistPath local-engine-onedir.spec
        if ($LASTEXITCODE -ne 0) {
            throw "local-engine onedir PyInstaller 빌드 실패 (exit=$LASTEXITCODE)"
        }
    } finally {
        Pop-Location
    }
} finally {
    $env:TEMP = $PreviousTemp
    $env:TMP = $PreviousTmp
    $env:PYINSTALLER_CONFIG_DIR = $PreviousPyInstallerConfig
}

$BuiltDir = Join-Path $DistPath "local-engine-onedir"
$BuiltExe = Join-Path $BuiltDir "local-engine.exe"
$InternalSource = Join-Path $BuiltDir "local-engine._internal"
if (-not (Test-Path $BuiltExe)) {
    throw "onedir executable이 없습니다: $BuiltExe"
}
if (-not (Test-Path $InternalSource)) {
    throw "onedir runtime directory가 없습니다: $InternalSource"
}
if (-not (Test-Path (Join-Path $InternalSource "python311.dll"))) {
    throw "onedir Python runtime이 없습니다: python311.dll"
}

$StagedExe = Join-Path $StageDir "local-engine-$TargetTriple.exe"
$InternalDest = Join-Path $StageDir "local-engine._internal"
Copy-Item $BuiltExe $StagedExe
Copy-Item $InternalSource $InternalDest -Recurse

$FileCount = (Get-ChildItem $InternalDest -Recurse -File).Count
if ($FileCount -le 0) {
    throw "staged onedir runtime이 비어 있습니다"
}
Write-Host "onedir sidecar: $StagedExe"
Write-Host "onedir runtime: $InternalDest ($FileCount files)"
Write-Host "tauri overlay: desktop/src-tauri/tauri.onedir.conf.json"
