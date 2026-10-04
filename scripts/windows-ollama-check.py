"""Windows Ollama/AMD GPU 환경 확인 스크립트.

실행:
    python scripts/windows-ollama-check.py

출력 기준:
- /api/ps 의 size_vram > 0 이면 GPU 사용 중
- size_vram == 0 이면 CPU 전용 폴백
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "local-engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from ollama_diagnostics import full_diagnostics  # type: ignore[import-not-found]  # noqa: E402


def main() -> int:
    data = full_diagnostics()
    print(json.dumps(data, ensure_ascii=False, indent=2))

    ollama = data.get("ollama", {})
    if not ollama.get("connected"):
        print("\n판정: Ollama 연결 실패")
        return 2
    if ollama.get("gpu_active"):
        print("\n판정: GPU 사용 중")
    else:
        print("\n판정: CPU 전용 실행 중")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
