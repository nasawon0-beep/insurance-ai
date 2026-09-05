#!/bin/bash
# 보험 AI — 시작 (macOS)
# Ollama 확인/기동 → (선택) control-server → local-engine → /health 대기 → 앱 실행.
# 로그는 repo/logs/ 에 쌓인다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
VENV_PY="$ROOT/local-engine/.venv/bin/python"
CONTROL_PY="$ROOT/control-server/.venv/bin/python"
ENGINE_PORT=8420
CONTROL_PORT=8790

log(){ printf '%s  %s\n' "$(date '+%H:%M:%S')" "$*"; }

if [ ! -x "$VENV_PY" ]; then
  log "오류: $VENV_PY 가 없습니다. 먼저 setup-once.command 를 실행하세요."
  exit 1
fi

# --- Ollama ---
if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
  log "Ollama OK"
else
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama 기동..."
    nohup ollama serve >"$LOGS/ollama.log" 2>&1 &
    for _ in $(seq 1 30); do
      curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1 && break
      sleep 1
    done
  else
    log "경고: 'ollama' 명령을 찾을 수 없습니다. setup-once.command 를 먼저 실행하세요."
  fi
fi

# --- 로컬 control-server: 기본 실행. 끄려면 START_CONTROL_SERVER=0 ---
if [ "${START_CONTROL_SERVER:-1}" != "0" ]; then
  if lsof -ti "tcp:$CONTROL_PORT" >/dev/null 2>&1; then
    log "control-server 이미 실행 중 (:$CONTROL_PORT)"
  else
    SECRETS="$ROOT/control-server/secrets.env"
    if [ ! -f "$SECRETS" ]; then
      log "control-server 시크릿 생성 (최초 1회): $SECRETS"
      JWT=$("$VENV_PY" -c 'import secrets;print(secrets.token_hex(32))')
      ADM=$("$VENV_PY" -c 'import secrets;print(secrets.token_hex(32))')
      umask 177
      cat > "$SECRETS" <<EOF
# 이 PC 전용. 커밋·공유 금지. 삭제하면 다음 실행에 재생성됨(기존 로그인 토큰 무효).
CONTROL_JWT_SECRET=$JWT
CONTROL_ADMIN_TOKEN=$ADM
# 데스크톱 빌드의 VITE_LICENSE_SECRET 과 반드시 동일해야 오프라인 라이선스(P0-B)가 검증됨.
# 파일럿은 둘 다 아래 기본값. 정식 배포 시 양쪽을 같은 강한 값으로 교체.
CONTROL_LICENSE_SECRET=dev-license-secret-change-me
EOF
      chmod 600 "$SECRETS"
    fi
    set -a; . "$SECRETS"; set +a
    if [ ! -x "$CONTROL_PY" ]; then
      log "오류: control-server/.venv 가 없습니다. setup-once.command 를 먼저 실행하세요."
      exit 1
    fi
    # 로컬 파일럿: 로그인 전 계정 복구 활성화 (loopback 게이트가 원격 차단)
    export CONTROL_LOCAL_RECOVERY=1
    log "control-server 시작 (:$CONTROL_PORT)"
    ( cd "$ROOT/control-server" && nohup "$CONTROL_PY" main.py >"$LOGS/control-server.log" 2>&1 & )
  fi
fi

# --- local-engine ---
health(){ curl -sf "http://127.0.0.1:$ENGINE_PORT/health" >/dev/null 2>&1; }
if lsof -ti "tcp:$ENGINE_PORT" >/dev/null 2>&1; then
  if health; then
    log "local-engine 이미 실행 중 — 재사용 (:$ENGINE_PORT)"
  else
    log "오류: 포트 $ENGINE_PORT 를 다른 프로그램이 점유 중입니다. 정리 후 다시 시도하세요."
    exit 1
  fi
else
  log "local-engine 시작 (:$ENGINE_PORT)"
  ( cd "$ROOT/local-engine" && nohup "$VENV_PY" main.py >"$LOGS/local-engine.log" 2>&1 & )
  for _ in $(seq 1 60); do health && break; sleep 1; done
  if ! health; then
    log "오류: local-engine 가 응답하지 않습니다. $LOGS/local-engine.log 를 확인하세요."
    exit 1
  fi
fi
log "엔진 준비 완료 (http://127.0.0.1:$ENGINE_PORT/health)"

# --- 앱 실행 ---
APP="/Applications/Insurance AI.app"
if [ -d "$APP" ]; then
  log "앱 실행: $APP"
  open "$APP"
elif [ -d "$ROOT/desktop" ] && command -v npm >/dev/null 2>&1; then
  log "데스크톱 앱(dev 모드) 실행 — 첫 실행은 의존성 설치로 시간이 걸립니다."
  ( cd "$ROOT/desktop" && nohup npm run tauri dev >"$LOGS/desktop.log" 2>&1 & )
else
  log "빌드된 앱이 없습니다. 개발 모드는 'cd desktop && npm run tauri dev' 로 실행하세요."
fi
log "완료."
