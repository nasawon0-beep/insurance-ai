@echo off
chcp 65001 >nul
REM 보험 AI - 정지 (Windows)
REM 포트 8420(local-engine) / 8790(control-server) 를 LISTEN 하는 PID 만 종료.
REM 둘 다 'python main.py' 라서 이름 기준 kill 은 쓰지 않는다.
setlocal EnableDelayedExpansion

for %%P in (8420 8790) do (
  set "FOUND="
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
    set "FOUND=1"
    echo 포트 %%P 종료: PID %%A
    taskkill /PID %%A /F >nul 2>&1
  )
  if not defined FOUND echo 포트 %%P: 실행 중인 프로세스 없음
)
echo 완료. (Ollama 와 데스크톱 앱 창은 그대로 둡니다.)
endlocal
