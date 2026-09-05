#!/bin/bash
# 보험 AI — 최초 1회 설치 (macOS)
# .venv 생성 + 의존성 설치 + Ollama 모델 내려받기 안내.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT/local-engine"

PY="${PYTHON_BIN:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "오류: python3 를 찾을 수 없습니다. Python 3.11(권장) 설치 후 다시 실행하세요."
  echo "  (엔진 venv 는 현재 3.9 로도 동작하지만 3.11 을 권장합니다.)"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "가상환경 생성: local-engine/.venv"
  "$PY" -m venv .venv
fi

echo "의존성 설치..."
"./.venv/bin/python" -m pip install --upgrade pip
"./.venv/bin/python" -m pip install -r requirements.txt

echo
echo "=== control-server 의존성 ==="
cd "$ROOT/control-server"
if [ ! -d ".venv" ]; then
  echo "가상환경 생성: control-server/.venv"
  "$PY" -m venv .venv
fi
"./.venv/bin/python" -m pip install --upgrade pip
"./.venv/bin/python" -m pip install -r requirements.txt
cd "$ROOT/local-engine"

echo
echo "=== Ollama 모델 ==="
if command -v ollama >/dev/null 2>&1; then
  echo "qwen2.5:7b 내려받기 (약 4.7GB)"
  ollama pull qwen2.5:7b
  echo "bge-m3 내려받기 (약 1.2GB)"
  ollama pull bge-m3
else
  echo "Ollama 가 설치돼 있지 않습니다."
  echo "  1) https://ollama.com/download 에서 설치"
  echo "  2) 터미널에서:"
  echo "       ollama pull qwen2.5:7b"
  echo "       ollama pull bge-m3"
fi

if ! command -v whisper-cli >/dev/null 2>&1; then
  echo
  echo "=== 음성 인식 파일 준비 ==="
  "./.venv/bin/python" -c "from whisper.transcriber import prewarm; prewarm(blocking=True)" || echo "[안내] 음성 인식 파일 내려받기에 실패했습니다. 인터넷 연결 후 앱을 켜면 자동으로 다시 받습니다."
fi

echo
echo "설치 완료. 이제 start.command 로 실행하세요."
