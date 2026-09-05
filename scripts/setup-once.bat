@echo off
chcp 65001 >nul
REM 보험 AI - 최초 1회 설치 (Windows)
REM Python(py 런처 포함) / Ollama 를 찾고, 없으면 설치 파일을 받아 실행한다.
REM 둘 다 준비되면 .venv 생성 + 의존성 설치 + 모델 pull 까지.
REM 설치 프로그램을 돌린 뒤에는 이 창을 닫고 setup-once.bat 을 다시 실행하세요.
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
for %%I in ("%HERE%..") do set "ROOT=%%~fI"
set "DL=%TEMP%\insurance-ai-setup"
if not exist "%DL%" mkdir "%DL%"

set "PY_VER=3.12.8"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "OLLAMA_URL=https://ollama.com/download/OllamaSetup.exe"

echo ============================================
echo   보험 AI - Windows 최초 설치
echo ============================================
echo.

REM ---------- 1) Python 찾기 (python / py -3 / python3 순) ----------
set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY ( py -3 --version >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python3 --version >nul 2>&1 && set "PY=python3" )

if not defined PY (
  echo [1/3] Python 을 찾지 못했습니다.
  echo   - 이미 설치돼 있는데 이 메시지가 나오면: PATH 에 안 잡힌 것입니다.
  echo     설치 프로그램이 "Modify" 로 뜨면 Modify 선택 -^> Next -^>
  echo     Advanced Options 에서 "Add Python to environment variables" 체크 -^> Install.
  echo   - 설치 안 돼 있으면: 아래에서 설치 파일을 받아 실행합니다.
  echo.
  if not exist "%DL%\python-setup.exe" (
    echo   다운로드 중...
    curl -L -o "%DL%\python-setup.exe" "%PY_URL%" 2>nul
    if not exist "%DL%\python-setup.exe" (
      powershell -NoProfile -Command "try{Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%DL%\python-setup.exe'}catch{}" 2>nul
    )
  )
  if not exist "%DL%\python-setup.exe" (
    echo   [오류] 자동 다운로드 실패. 브라우저로 직접 받아 설치하세요:
    echo     %PY_URL%
    echo   설치 시 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
    exit /b 1
  )
  echo   설치 프로그램을 엽니다. "Add python.exe to PATH" 체크 후 Install.
  start /wait "" "%DL%\python-setup.exe"
  echo.
  echo   ^>^> Python 설치가 끝났으면 이 창을 닫고 setup-once.bat 을 다시 실행하세요.
  pause
  exit /b 0
)
echo [1/3] Python OK  (%PY%)
%PY% --version

REM ---------- 2) Ollama ----------
set "OL_OK="
where ollama >nul 2>&1 && set "OL_OK=1"
if not defined OL_OK (
  echo [2/3] Ollama 를 찾지 못했습니다. 설치 파일을 받습니다...
  if not exist "%DL%\OllamaSetup.exe" (
    curl -L -o "%DL%\OllamaSetup.exe" "%OLLAMA_URL%" 2>nul
    if not exist "%DL%\OllamaSetup.exe" (
      powershell -NoProfile -Command "try{Invoke-WebRequest -Uri '%OLLAMA_URL%' -OutFile '%DL%\OllamaSetup.exe'}catch{}" 2>nul
    )
  )
  if not exist "%DL%\OllamaSetup.exe" (
    echo   [오류] 자동 다운로드 실패. 직접 받아 설치하세요: %OLLAMA_URL%
    pause
    exit /b 1
  )
  echo   Ollama 설치 프로그램을 엽니다. 안내대로 설치하세요.
  start /wait "" "%DL%\OllamaSetup.exe"
  echo.
  echo   ^>^> Ollama 설치가 끝났으면 이 창을 닫고 setup-once.bat 을 다시 실행하세요.
  pause
  exit /b 0
)
echo [2/3] Ollama OK

REM ---------- 3) 가상환경 + 의존성 + 모델 ----------
echo [3/3] 엔진 환경 설치...
cd /d "%ROOT%\local-engine"
if not exist ".venv\Scripts\python.exe" (
  echo   가상환경 생성: local-engine\.venv
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo   [오류] 가상환경 생성 실패. Python 설치를 확인하세요.
  pause
  exit /b 1
)
echo   의존성 설치 (몇 분 걸릴 수 있음)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   [오류] pip 설치 실패. 위 메시지를 확인하세요.
  pause
  exit /b 1
)

echo.
echo === control-server 의존성 ===
cd /d "%ROOT%\control-server"
if not exist ".venv\Scripts\python.exe" (
  echo   가상환경 생성: control-server\.venv
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo [오류] control-server 가상환경 생성 실패.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [오류] control-server 의존성 설치 실패. 네트워크 확인 후 다시 실행하세요.
  pause
  exit /b 1
)
cd /d "%ROOT%\local-engine"

echo.
echo   Ollama 모델 내려받기 (합쳐서 약 6.5GB, 최초 1회. 이미 있으면 건너뜀)...
ollama pull qwen2.5:7b
ollama pull bge-m3

echo.
echo === 음성 인식 파일 준비 ===
".venv\Scripts\python.exe" -c "from whisper.transcriber import prewarm; prewarm(blocking=True)"
if errorlevel 1 (
  echo [안내] 음성 인식 파일 내려받기에 실패했습니다. 인터넷 연결 후 앱을 켜면 자동으로 다시 받습니다.
)

echo.
echo ============================================
echo   설치 완료. 이제 start.bat 을 실행하세요.
echo ============================================
pause
endlocal
