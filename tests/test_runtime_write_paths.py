from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

import main
from database import coverage


def test_engine_warmup_zero_starts_no_model_warmup(monkeypatch):
    started = []

    class RecordingThread:
        def __init__(self, *, target, daemon):
            started.append((target, daemon))

        def start(self):
            return None

    monkeypatch.setenv("ENGINE_WARMUP", "0")
    monkeypatch.setattr(main.threading, "Thread", RecordingThread)

    main._start_model_warmups()

    assert started == []


def test_engine_warmup_one_keeps_ollama_and_whisper_prewarm(monkeypatch):
    started = []

    class RecordingThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.daemon))

    monkeypatch.setenv("ENGINE_WARMUP", "1")
    monkeypatch.setattr(main.threading, "Thread", RecordingThread)

    main._start_model_warmups()

    assert started == [(main._warm_ollama, True), (main._warm_whisper, True)]


def test_runtime_paths_respect_explicit_isolation_environment(monkeypatch, tmp_path):
    import runtime_paths

    model_dir = tmp_path / "explicit-models"
    log_dir = tmp_path / "explicit-logs"
    monkeypatch.setenv("WHISPER_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("COVERAGE_ANALYSIS_LOG_DIR", str(log_dir))

    assert runtime_paths.whisper_model_dir() == model_dir
    assert runtime_paths.coverage_log_dir() == log_dir


def test_windows_defaults_use_project_identity_not_frozen_install(monkeypatch, tmp_path):
    import runtime_paths

    appdata = tmp_path / "Roaming"
    frozen_install = tmp_path / "frozen" / "_internal"
    monkeypatch.delenv("WHISPER_MODEL_DIR", raising=False)
    monkeypatch.delenv("COVERAGE_ANALYSIS_LOG_DIR", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_install), raising=False)

    data_dir = runtime_paths.default_app_data_dir(
        environ={"APPDATA": str(appdata)}, platform="win32", home=tmp_path / "home"
    )

    assert data_dir == appdata / "com.abc.insurance-ai"
    assert runtime_paths.whisper_model_dir(
        environ={"APPDATA": str(appdata)}, platform="win32", home=tmp_path / "home"
    ) == data_dir / "whisper-models"
    assert runtime_paths.coverage_log_dir(
        environ={"APPDATA": str(appdata)}, platform="win32", home=tmp_path / "home"
    ) == data_dir / "logs"
    assert frozen_install not in runtime_paths.whisper_model_dir(
        environ={"APPDATA": str(appdata)}, platform="win32", home=tmp_path / "home"
    ).parents


def test_missing_windows_profile_environment_uses_home_fallback(tmp_path):
    import runtime_paths

    assert runtime_paths.default_app_data_dir(
        environ={}, platform="win32", home=tmp_path
    ) == tmp_path / "AppData" / "Roaming" / "com.abc.insurance-ai"


def test_unwritable_coverage_log_env_does_not_fallback_or_leak_customer_id(
    monkeypatch, tmp_path, caplog
):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    requested = blocker / "isolated-logs"
    monkeypatch.setenv("COVERAGE_ANALYSIS_LOG_DIR", str(requested))

    with caplog.at_level(logging.INFO):
        coverage._append_analysis_log("private-customer-id", "analysis_start", doc_ids=["private-doc"])

    assert not requested.exists()
    assert "private-customer-id" not in caplog.text
    assert "private-doc" not in caplog.text


def test_coverage_log_body_keeps_only_operational_fields(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("COVERAGE_ANALYSIS_LOG_DIR", str(log_dir))

    coverage._append_analysis_log(
        "private-customer-id",
        "documents_loaded",
        message="private free-form text",
        doc_ids=["private-doc"],
        run_id="private-run",
        doc_count=3,
        elapsed_ms=7,
    )

    payload = (log_dir / "coverage_analysis_private-customer-id.log").read_text(
        encoding="utf-8"
    )
    assert '"event": "documents_loaded"' in payload
    assert '"doc_count": 3' in payload
    assert '"elapsed_ms": 7' in payload
    assert "private free-form text" not in payload
    assert "private-doc" not in payload
    assert "private-run" not in payload


def test_tauri_passes_profile_log_dir_to_coverage_sidecar():
    source = (
        Path(__file__).resolve().parents[1]
        / "desktop"
        / "src-tauri"
        / "src"
        / "lib.rs"
    ).read_text(encoding="utf-8")

    assert '.env("COVERAGE_ANALYSIS_LOG_DIR", &profile.log_dir)' in source
