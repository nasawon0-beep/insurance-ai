"""
AI 분류 프롬프트 패키지.

기존 `database/import_data.py` 모듈 이름과 Phase 2-C용
`database/import_data/` 패키지 이름이 겹치므로, 공개 API는 기존 모듈의
preview/commit/예외를 그대로 다시 내보내고 새 AI 분류기는 하위 모듈로 제공한다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_LEGACY_PATH = Path(__file__).resolve().with_suffix("").parent.parent / "import_data.py"
_SPEC = importlib.util.spec_from_file_location("database._import_data_legacy", _LEGACY_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import 구조 훼손 방어
    raise ImportError(f"cannot load legacy import_data module: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

# 기존 CSV/XLSX import API 호환
preview = _legacy.preview
commit = _legacy.commit
decode = _legacy.decode
_InvalidImport = _legacy._InvalidImport
_NameUnmapped = _legacy._NameUnmapped
_XlsxReadError = _legacy._XlsxReadError

__all__ = [
    "preview",
    "commit",
    "decode",
    "_InvalidImport",
    "_NameUnmapped",
    "_XlsxReadError",
    "ai_classifier",
]


def __getattr__(name: str):
    """기존 `database.import_data` 내부 헬퍼 테스트와 호출부 호환."""
    return getattr(_legacy, name)
