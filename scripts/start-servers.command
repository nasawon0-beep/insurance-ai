#!/bin/bash
# 보험 AI — 로컬 서버 상주 (macOS, LaunchAgent 전용)
# start.command 의 서버 기동 로직만 떼어냈다. 앱은 열지 않는다.
#   - control-server(:8790)  로그인/라이선스
#   - local-engine(:8420)    고객 데이터/검색
#   - Ollama(:11434)         RAG/요약 (설치돼 있을 때만)
#
# 종료 코드 규약 (plist KeepAlive={SuccessfulExit:false} 와 짝):
#   exit 0  → 준비물 미비 등 "재기동해도 소용없는" 상태. launchd 가 다시 띄우지 않는다.
#   exit 1  → 서버가 죽었다. launchd 가 재기동한다.
# 재진입 시 포트/health 검사로 살아있는 서버는 건드리지 않는다(멱등).
set -uo pipefail

# scripts/ 안에 있으므로 상대경로로 repo 루트를 찾는다. repo 를 옮겨도 동작한다.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOGS="$ROOT/logs"
VENV_PY="$ROOT/local-engine/.venv/bin/python"
CONTROL_PY="$ROOT/control-server/.venv/bin/python"
SECRETS="$ROOT/control-server/secrets.env"
ENGINE_PORT=8420
CONTROL_PORT=8790
mkdir -p "$LOGS"

log(){ printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
port_open(){ lsof -ti "tcp:$1" >/dev/null 2>&1; }
engine_health(){ curl -sf "http://127.0.0.1:$ENGINE_PORT/health" >/dev/null 2>&1; }
control_health(){ curl -sf "http://127.0.0.1:$CONTROL_PORT/health" >/dev/null 2>&1; }

# --- 준비물 확인: 없으면 "영구 오류" 로 보고 exit 0 (launchd 재기동 안 함) ---
if [ ! -x "$VENV_PY" ] || [ ! -x "$CONTROL_PY" ]; then
  log "중단: 서버 venv 없음. scripts/setup-once.command 실행 후 로그인 다시 하거나"
  log "      launchctl kickstart -k gui/\$(id -u)/com.abc.insurance-ai.servers 로 재시도하세요."
  exit 0
fi
if [ ! -f "$SECRETS" ]; then
  log "중단: $SECRETS 없음. 최초 1회 scripts/start.command 로 시크릿을 생성한 뒤 재시도하세요."
  exit 0
fi

# --- 한 포트의 상태를 판정: 0=healthy(재사용) 1=닫힘(기동해야) 2=점유but unhealthy(사람 개입) ---
port_state(){ # $1 port, $2 health-fn
  if ! port_open "$1"; then return 1; fi
  if "$2"; then return 0; fi
  return 2
}

# --- Ollama (있을 때만) ---
if ! curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama 기동..."
    nohup ollama serve >"$LOGS/ollama.log" 2>&1 &
  else
    log "경고: 'ollama' 명령 없음 — RAG/요약만 비활성. 로그인·고객관리는 정상."
  fi
fi

# --- control-server ---
port_state "$CONTROL_PORT" control_health; cs=$?
case $cs in
  0) log "control-server 이미 정상 — 재사용 (:$CONTROL_PORT)" ;;
  2) log "중단: 포트 $CONTROL_PORT 를 응답 없는 프로세스가 점유 중. 해당 PID 종료 후 재시도(수동 개입 필요)."
     lsof -nP -iTCP:"$CONTROL_PORT" -sTCP:LISTEN 2>/dev/null | tail -n +1 | while read -r l; do log "  $l"; done
     exit 0 ;;
  1) set -a; . "$SECRETS"; set +a
     export CONTROL_LOCAL_RECOVERY=1   # 로컬: 로그인 전 계정 복구 허용 (loopback 게이트가 원격 차단)
     log "control-server 시작 (:$CONTROL_PORT)"
     ( cd "$ROOT/control-server" && exec "$CONTROL_PY" main.py ) >>"$LOGS/control-server.log" 2>&1 & ;;
esac

# --- local-engine ---
port_state "$ENGINE_PORT" engine_health; es=$?
case $es in
  0) log "local-engine 이미 정상 — 재사용 (:$ENGINE_PORT)" ;;
  2) log "중단: 포트 $ENGINE_PORT 를 응답 없는 프로세스가 점유 중. 해당 PID 종료 후 재시도(수동 개입 필요)."
     lsof -nP -iTCP:"$ENGINE_PORT" -sTCP:LISTEN 2>/dev/null | while read -r l; do log "  $l"; done
     exit 0 ;;
  1) log "local-engine 시작 (:$ENGINE_PORT)"
     ( cd "$ROOT/local-engine" && exec "$VENV_PY" main.py ) >>"$LOGS/local-engine.log" 2>&1 & ;;
esac

# --- health 대기 (방금 띄운 서버 기동 확인) ---
for _ in $(seq 1 60); do
  control_health && engine_health && break
  sleep 1
done
if ! control_health || ! engine_health; then
  log "오류: 서버 health 확인 실패. $LOGS/ 의 control-server.log·local-engine.log 확인. (재기동 유도)"
  exit 1
fi
log "서버 준비 완료 (control :$CONTROL_PORT, engine :$ENGINE_PORT)"

# --- 상주: 한 서버라도 죽으면 exit 1 → launchd 가 재기동 ---
while control_health && engine_health; do
  sleep 10
done
log "경고: 서버 health 중단 감지 — 재기동 유도(exit 1)."
exit 1
