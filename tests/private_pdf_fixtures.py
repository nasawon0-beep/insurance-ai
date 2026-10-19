"""Resolve optional private PDF fixtures by anonymous aliases.

The alias-to-path map is intentionally outside Git. Tests that require a real
PDF skip explicitly when PRIVATE_PDF_FIXTURE_MAP is not configured, while all
synthetic/unit tests remain runnable in clean CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_MAP_ENV = "PRIVATE_PDF_FIXTURE_MAP"
_SKIP_REASON = "private real-PDF fixture unavailable; synthetic and contract tests still run"


def private_pdf(alias: str) -> Path:
    map_path = os.environ.get(_MAP_ENV, "").strip()
    if not map_path:
        pytest.skip(_SKIP_REASON)
        raise RuntimeError("unreachable")
    path = Path(map_path)
    if not path.is_file():
        pytest.skip(_SKIP_REASON)
        raise RuntimeError("unreachable")
    mapping: object = {}
    try:
        mapping = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pytest.skip(_SKIP_REASON)
        raise RuntimeError("unreachable")
    raw = mapping.get(alias) if isinstance(mapping, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        pytest.skip(_SKIP_REASON)
        raise RuntimeError("unreachable")
    fixture = Path(raw)
    if fixture.suffix.lower() != ".pdf" or not fixture.is_file():
        pytest.skip(_SKIP_REASON)
        raise RuntimeError("unreachable")
    return fixture
