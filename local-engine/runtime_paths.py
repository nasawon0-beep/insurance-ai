"""Writable runtime locations for the local engine.

Bundled resources are installation inputs only. Runtime caches and diagnostic
logs must live under the current user's app data directory unless an explicit
isolation path is supplied by the desktop runtime or QA harness.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

APP_IDENTIFIER = "com.abc.insurance-ai"


def default_app_data_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    current_platform = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else Path(home)

    if current_platform == "win32":
        base = env.get("APPDATA") or env.get("LOCALAPPDATA")
        root = Path(base) if base else user_home / "AppData" / "Roaming"
    elif current_platform == "darwin":
        root = user_home / "Library" / "Application Support"
    else:
        base = env.get("XDG_DATA_HOME")
        root = Path(base) if base else user_home / ".local" / "share"
    return root / APP_IDENTIFIER


def _explicit_or_default(
    variable: str,
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    explicit = env.get(variable)
    if explicit:
        return Path(explicit)
    return default_app_data_dir(environ=env, platform=platform, home=home) / suffix


def whisper_model_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    return _explicit_or_default(
        "WHISPER_MODEL_DIR",
        "whisper-models",
        environ=environ,
        platform=platform,
        home=home,
    )


def coverage_log_dir(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    return _explicit_or_default(
        "COVERAGE_ANALYSIS_LOG_DIR",
        "logs",
        environ=environ,
        platform=platform,
        home=home,
    )
