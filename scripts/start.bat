@echo off
chcp 65001 >nul
REM 보험 AI - 시작 (Windows)
REM Ollama 확인/기동 -> (선택) control-server -> local-engine -> /health 대기 -> 앱 실행.
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
set "LOGS=%ROOT%\logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set "VENV_PY=%ROOT%\local-engine\.venv\Scripts\python.exe"
set "CONTROL_PY=%ROOT%\control-server\.venv\Scripts\python.exe"
set "ENGINE_PORT=8420"
set "CONTROL_PORT=8790"

if not exist "%VENV_PY%" (
  echo [오류] "%VENV_PY%" 가 없습니다. 먼저 setup-once.bat 을 실행하세요.
  pause
  exit /b 1
)

REM --- Ollama ---
curl -sf "http://localhost:11434/api/tags" >nul 2>&1
if not errorlevel 1 (
  echo Ollama OK
  goto after_ollama
)
where ollama >nul 2>&1
if errorlevel 1 (
  echo [경고] 'ollama' 를 찾을 수 없습니다. setup-once.bat 을 먼저 실행하세요.
  goto after_ollama
)
echo Ollama 기동...
start "insurance-ai ollama" /b cmd /c "ollama serve > "%LOGS%\ollama.log" 2>&1"
set /a _tries=0
:wait_ollama
curl -sf "http://localhost:11434/api/tags" >nul 2>&1
if not errorlevel 1 goto after_ollama
set /a _tries+=1
if %_tries% GEQ 30 goto after_ollama
ping -n 2 127.0.0.1 >nul
goto wait_ollama
:after_ollama

REM --- 로컬 control-server: 기본 실행. 끄려면 set START_CONTROL_SERVER=0 ---
if not "%START_CONTROL_SERVER%"=="0" (
  netstat -ano | findstr ":%CONTROL_PORT% " | findstr LISTENING >nul 2>&1
  if errorlevel 1 (
    set "SECRETS=%ROOT%\control-server\secrets.env"
    if not exist "!SECRETS!" (
      echo control-server 시크릿 생성 ^(최초 1회^): !SECRETS!
      for /f %%i in ('""%VENV_PY%" -c "import secrets;print(secrets.token_hex(32))""') do set "JWT=%%i"
      for /f %%i in ('""%VENV_PY%" -c "import secrets;print(secrets.token_hex(32))""') do set "ADM=%%i"
      > "!SECRETS!" echo # 이 PC 전용. 커밋·공유 금지. 삭제하면 다음 실행에 재생성됨.
      >> "!SECRETS!" echo CONTROL_JWT_SECRET=!JWT!
      >> "!SECRETS!" echo CONTROL_ADMIN_TOKEN=!ADM!
      >> "!SECRETS!" echo # 데스크톱 빌드의 VITE_LICENSE_SECRET 과 반드시 동일해야 오프라인 라이선스가 검증됨.
      >> "!SECRETS!" echo CONTROL_LICENSE_SECRET=dev-license-secret-change-me
    )
    for /f "usebackq tokens=1,* delims==" %%a in ("!SECRETS!") do (
      echo %%a| findstr /b "#" >nul || set "%%a=%%b"
    )
    if not exist "!CONTROL_PY!" (
      echo [오류] control-server\.venv 가 없습니다. setup-once.bat 을 먼저 실행하세요.
      pause
      exit /b 1
    )
    REM 로컬 파일럿: 로그인 전 계정 복구 활성화 (loopback 게이트가 원격 차단)
    set "CONTROL_LOCAL_RECOVERY=1"
    echo control-server 시작 :%CONTROL_PORT%
    start "insurance-ai control-server" /b cmd /c "cd /d "%ROOT%\control-server" && "!CONTROL_PY!" main.py > "%LOGS%\control-server.log" 2>&1"
  ) else (
    echo control-server 이미 실행 중 :%CONTROL_PORT%
  )
)

REM --- local-engine ---
netstat -ano | findstr ":%ENGINE_PORT% " | findstr LISTENING >nul 2>&1
if errorlevel 1 goto start_engine
curl -sf "http://127.0.0.1:%ENGINE_PORT%/health" >nul 2>&1
if errorlevel 1 (
  echo [오류] 포트 %ENGINE_PORT% 를 다른 프로그램이 점유 중입니다. 정리 후 다시 시도하세요.
  pause
  exit /b 1
)
echo local-engine 이미 실행 중 - 재사용 :%ENGINE_PORT%
goto engine_ready

:start_engine
echo local-engine 시작 :%ENGINE_PORT%
start "insurance-ai local-engine" /b cmd /c "cd /d "%ROOT%\local-engine" && "%VENV_PY%" main.py > "%LOGS%\local-engine.log" 2>&1"
set /a _tries=0
:wait_engine
curl -sf "http://127.0.0.1:%ENGINE_PORT%/health" >nul 2>&1
if not errorlevel 1 goto engine_ready
set /a _tries+=1
if %_tries% GEQ 60 (
  echo [오류] local-engine 가 응답하지 않습니다. "%LOGS%\local-engine.log" 확인.
  pause
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto wait_engine

:engine_ready
echo 엔진 준비 완료.

REM --- 앱 실행 ---
if exist "%ROOT%\desktop\src-tauri\target\release\Insurance AI.exe" (
  start "" "%ROOT%\desktop\src-tauri\target\release\Insurance AI.exe"
  goto done
)
if exist "%ROOT%\desktop" (
  where npm >nul 2>&1
  if not errorlevel 1 (
    echo 데스크톱 앱 dev 모드 실행 - 첫 실행은 의존성 설치로 시간이 걸립니다.
    start "insurance-ai desktop" /b cmd /c "cd /d "%ROOT%\desktop" && npm run tauri dev > "%LOGS%\desktop.log" 2>&1"
  ) else (
    echo [안내] Node/npm 이 필요합니다. 또는 빌드된 .msi 를 설치하세요.
  )
)
:done
echo 완료.
endlocal
