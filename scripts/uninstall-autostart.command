#!/bin/bash
# 보험 AI — 서버 자동기동 LaunchAgent 제거 (macOS).
# launchctl bootout 은 자동으로 하지 않는다 — 아래 명령을 직접 실행하면 즉시 내려간다.
# plist 파일은 삭제한다. 이미 떠 있는 서버 프로세스는 stop.command 로 멈춘다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.abc.insurance-ai.servers"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "먼저 에이전트를 내리려면:"
echo "  launchctl bootout gui/$(id -u)/$LABEL"
echo

if [ -f "$DEST" ]; then
  rm -f "$DEST"
  echo "삭제됨: $DEST"
else
  echo "이미 없음: $DEST"
fi
echo
echo "실행 중인 서버도 멈추려면:  \"$HERE/stop.command\""
