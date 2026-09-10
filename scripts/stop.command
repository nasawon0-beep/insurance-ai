#!/bin/bash
# 보험 AI — 정지 (macOS)
# 포트 8420(local-engine) / 8790(control-server) 를 점유한 PID 만 종료한다.
# 주의: local-engine 과 control-server 는 둘 다 'python main.py' 라서
#       'pkill -f main.py' 는 절대 쓰지 않는다 (둘 다 죽는다).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib/servers.sh"   # ENGINE_PORT / CONTROL_PORT

for PORT in "$ENGINE_PORT" "$CONTROL_PORT"; do
  PIDS="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
  if [ -n "$PIDS" ]; then
    echo "포트 $PORT 종료: $PIDS"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    sleep 1
    STILL="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    if [ -n "$STILL" ]; then
      echo "  강제 종료: $STILL"
      # shellcheck disable=SC2086
      kill -9 $STILL 2>/dev/null || true
    fi
  else
    echo "포트 $PORT: 실행 중인 프로세스 없음"
  fi
done
echo "완료. (Ollama 와 데스크톱 앱 창은 그대로 둡니다.)"
