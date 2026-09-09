#!/bin/bash
# 보험 AI — 서버 자동기동 LaunchAgent 설치 (macOS).
# plist 의 __ROOT__ 를 이 repo 경로로 치환해 ~/Library/LaunchAgents/ 에 복사한다.
# launchctl 로드는 자동으로 하지 않는다 — 아래 출력되는 명령을 직접 실행하거나
# 다음 로그인 때 자동 적용된다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LABEL="com.abc.insurance-ai.servers"
SRC="$HERE/$LABEL.plist"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST="$DEST_DIR/$LABEL.plist"

[ -f "$SRC" ] || { echo "오류: $SRC 없음" >&2; exit 1; }
mkdir -p "$DEST_DIR" "$ROOT/logs"

sed "s#__ROOT__#$ROOT#g" "$SRC" > "$DEST"
chmod 644 "$DEST"

echo "설치됨: $DEST   (ROOT = $ROOT)"
echo
echo "지금 바로 켜려면 (안 하면 다음 로그인 때 자동 적용):"
echo "  launchctl bootstrap gui/$(id -u) \"$DEST\""
echo "  launchctl kickstart -k gui/$(id -u)/$LABEL"
echo
echo "상태:  launchctl print gui/$(id -u)/$LABEL | head"
echo "로그:  tail -f \"$ROOT/logs/launchagent.log\""
