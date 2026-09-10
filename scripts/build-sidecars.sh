#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONTROL_DIR="$ROOT/control-server"
LOCAL_ENGINE_DIR="$ROOT/local-engine"
CONTROL_PYTHON="$CONTROL_DIR/.venv/bin/python"
LOCAL_ENGINE_PYTHON="$LOCAL_ENGINE_DIR/.venv/bin/python"
TARGET_TRIPLE=$(rustc -Vv | sed -n 's/^host: //p')
DEST_DIR="$ROOT/desktop/src-tauri/binaries"
export PYINSTALLER_CONFIG_DIR="${TMPDIR:-/tmp}/insurance-ai-pyinstaller"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

if [[ -z "$TARGET_TRIPLE" ]]; then
  echo "rustc host target triple을 확인할 수 없습니다." >&2
  exit 1
fi

if [[ ! -x "$CONTROL_PYTHON" ]]; then
  python3 -m venv "$CONTROL_DIR/.venv"
fi
"$CONTROL_PYTHON" -m pip install -r "$CONTROL_DIR/requirements.txt"

# pyinstaller 가 이미 맞는 버전이면 재설치 생략 (오프라인·인덱스 상태 의존 축소)
if ! "$CONTROL_PYTHON" -c 'import PyInstaller, sys; sys.exit(0 if PyInstaller.__version__.split(".")[0] == "6" else 1)' 2>/dev/null; then
  "$CONTROL_PYTHON" -m pip install 'pyinstaller>=6,<7'
fi
(
  cd "$CONTROL_DIR"
  "$CONTROL_PYTHON" -m PyInstaller --clean --noconfirm control-server.spec
)

if [[ ! -x "$LOCAL_ENGINE_PYTHON" ]]; then
  python3 -m venv "$LOCAL_ENGINE_DIR/.venv"
fi
"$LOCAL_ENGINE_PYTHON" -m pip install -r "$LOCAL_ENGINE_DIR/requirements.txt"

if ! "$LOCAL_ENGINE_PYTHON" -c 'import PyInstaller, sys; sys.exit(0 if PyInstaller.__version__.split(".")[0] == "6" else 1)' 2>/dev/null; then
  "$LOCAL_ENGINE_PYTHON" -m pip install 'pyinstaller>=6,<7'
fi
(
  cd "$LOCAL_ENGINE_DIR"
  "$LOCAL_ENGINE_PYTHON" -m PyInstaller --clean --noconfirm local-engine.spec
)

mkdir -p "$DEST_DIR"

# 기존 파일을 덮어쓰기(cp)만 하면 macOS AMFI 가 이전 ad-hoc 서명 cdhash 를 그 경로에
# 캐시해 둔 탓에 새 바이너리를 조용히 SIGKILL 한다. rm(새 inode) → cp → 재서명 으로 회피.
install_sidecar() {
  local src="$1" dst="$2"
  rm -f "$dst"
  cp "$src" "$dst"
  chmod 755 "$dst"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    codesign --force --sign - "$dst"
  fi
  echo "sidecar: $dst"
}

install_sidecar "$CONTROL_DIR/dist/control-server" "$DEST_DIR/control-server-$TARGET_TRIPLE"
install_sidecar "$LOCAL_ENGINE_DIR/dist/local-engine" "$DEST_DIR/local-engine-$TARGET_TRIPLE"
