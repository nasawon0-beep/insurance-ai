"""Resolve optional private PDF fixtures through anonymous aliases.

The alias-to-path manifest remains outside the repository. Tests that require a
local real PDF skip at the individual test call when the manifest or fixture is
unavailable. Synthetic and contract tests continue to collect and run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NoReturn

import pytest

PRIVATE_PDF_FIXTURE_MAP_ENV = "PRIVATE_PDF_FIXTURE_MAP"
_SKIP_REASON = "private real-PDF fixture unavailable; synthetic and contract tests still run"


def _skip_unavailable() -> NoReturn:
    pytest.skip(_SKIP_REASON)
    raise RuntimeError("unreachable")


def private_pdf(alias: str) -> Path:
    manifest_value = os.environ.get(PRIVATE_PDF_FIXTURE_MAP_ENV, "").strip()
    if not manifest_value:
        _skip_unavailable()

    manifest_path = Path(manifest_value)
    if not manifest_path.is_file():
        _skip_unavailable()

    mapping: object = {}
    try:
        mapping = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _skip_unavailable()

    raw_path = mapping.get(alias) if isinstance(mapping, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        _skip_unavailable()

    fixture_path = Path(raw_path)
    if fixture_path.suffix.lower() != ".pdf" or not fixture_path.is_file():
        _skip_unavailable()
    return fixture_path
