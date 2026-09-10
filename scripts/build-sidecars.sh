#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONTROL_DIR="$ROOT/control-server"
PYTHON="$CONTROL_DIR/.venv/bin/python"
TARGET_TRIPLE=$(rustc -Vv | awk '/^host:/ { print $2 }')
DEST_DIR="$ROOT/desktop/src-tauri/binaries"

if [[ -z "$TARGET_TRIPLE" ]]; then
  echo "rustc host target triple을 확인할 수 없습니다." >&2
  exit 1
fi

# pyinstaller 가 이미 맞는 버전이면 재설치 생략 (오프라인·인덱스 상태 의존 축소)
if ! "$PYTHON" -c 'import PyInstaller, sys; sys.exit(0 if PyInstaller.__version__.split(".")[0] == "6" else 1)' 2>/dev/null; then
  "$PYTHON" -m pip install 'pyinstaller>=6,<7'
fi
(
  cd "$CONTROL_DIR"
  "$PYTHON" -m PyInstaller --clean --noconfirm control-server.spec
)

mkdir -p "$DEST_DIR"
cp "$CONTROL_DIR/dist/control-server" "$DEST_DIR/control-server-$TARGET_TRIPLE"
chmod 755 "$DEST_DIR/control-server-$TARGET_TRIPLE"

echo "sidecar: $DEST_DIR/control-server-$TARGET_TRIPLE"
