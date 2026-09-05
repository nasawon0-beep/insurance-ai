"""
로컬 STT. 아키텍처의 whisper/ 모듈.

백엔드 2개, WHISPER_BACKEND 로 선택 (기본 auto):
  - cli    : whisper.cpp(whisper-cli). Apple Silicon 은 Metal GPU 가속 → CPU 대비 수 배 빠름.
  - faster : faster-whisper(CTranslate2, CPU). CLI 가 없을 때 폴백.
  - auto   : whisper-cli 가 PATH 에 있으면 cli, 없으면 faster.

CLI 모델: GGML 포맷. 기본 whisper/models/ggml-large-v3-turbo.bin (env WHISPER_GGML_MODEL).
오디오는 ffmpeg 로 16kHz mono wav 로 변환 후 넘긴다 (whisper-cli 요구).
"""
from __future__ import annotations

import json
import errno
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from huggingface_hub.utils import tqdm as hub_tqdm

try:
    import requests
except ImportError:  # optional dependency
    requests = None

try:
    import httpx
except ImportError:  # optional dependency
    httpx = None

_MODEL_DIR = Path(os.environ.get("WHISPER_MODEL_DIR", str(Path(__file__).parent / "models")))
_MIN_FREE_BYTES = 1_500_000_000

# --- CLI (whisper.cpp) 설정 ---
WHISPER_CLI = os.environ.get("WHISPER_CLI", "whisper-cli")
GGML_MODEL = os.environ.get(
    "WHISPER_GGML_MODEL", str(_MODEL_DIR / "ggml-large-v3-turbo.bin")
)
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", str(os.cpu_count() or 8)))

# --- faster-whisper 폴백 설정 ---
# CLI(whisper.cpp) 가 없는 PC(주로 Windows)의 기본값은 "small".
# base 는 한국어 정확도가 약해 기본값은 small 이다.
# 예전 WHISPER_MODEL 도 계속 지원 (macOS CLI 경로에는 영향 없음).
FW_MODEL_SIZE = os.environ.get(
    "WHISPER_FASTER_MODEL", os.environ.get("WHISPER_MODEL", "small")
)
FW_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
FW_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
FW_BEAM = int(os.environ.get("WHISPER_BEAM", "1"))

_dl = {
    "state": "absent", "pct": None, "mb": 0, "total_mb": 0,
    "reason": None, "detail": None, "started_at": None, "updated_at": None,
}
_dl_lock = threading.Lock()
_load_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelNotReady(Exception):
    pass


class _ProgressTqdm(hub_tqdm):
    _agg = {"seen": 0.0, "total": 0.0}

    def __init__(self, *a, **kw):
        self._download_n = float(kw.get("initial", 0) or 0)
        self._download_total = 0.0
        try:
            super().__init__(*a, **kw)
        except Exception:
            pass
        self._sync_total()

    def _sync_total(self):
        total = float(getattr(self, "total", 0) or 0)
        with _dl_lock:
            self._agg["total"] += total - self._download_total
            self._download_total = total

    def update(self, n=1):
        try:
            result = super().update(n)
        except Exception:
            result = None
        try:
            self._download_n += float(n or 0)
            total = float(getattr(self, "total", 0) or 0)
            with _dl_lock:
                self._agg["seen"] += float(n or 0)
                self._agg["total"] += total - self._download_total
                self._download_total = total
                seen = self._agg["seen"]
                aggregate_total = self._agg["total"]
                _dl["mb"] = round(seen / 1_000_000)
                _dl["total_mb"] = round(aggregate_total / 1_000_000) if aggregate_total else 0
                pct = min(100, round(100 * seen / aggregate_total)) if aggregate_total else None
                _dl["pct"] = pct
                _dl["updated_at"] = _now()
        except Exception:
            pass
        return result


def _free_bytes() -> int:
    for directory in [_MODEL_DIR, *_MODEL_DIR.parents]:
        if directory.exists():
            return shutil.disk_usage(directory).free
    raise FileNotFoundError(f"no existing parent directory for {_MODEL_DIR}")


def _classify(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "disk"
    try:
        if _free_bytes() < _MIN_FREE_BYTES:
            return "disk"
    except OSError:
        pass
    if isinstance(exc, HTTPError) and exc.code in {403, 407}:
        return "blocked"
    if requests is not None:
        if isinstance(exc, (requests.exceptions.ProxyError, requests.exceptions.ReadTimeout)):
            return "blocked"
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout)):
            return "network"
    if httpx is not None:
        if isinstance(exc, (httpx.ProxyError, httpx.ReadTimeout)):
            return "blocked"
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "network"
    if "403" in text or "407" in text or "proxy" in text or "readtimeout" in text or "read timed out" in text:
        return "blocked"
    if isinstance(exc, (URLError, ConnectionError)) or any(
        marker in text for marker in ("name or service not known", "temporary failure in name resolution", "nodename nor servname")
    ):
        return "network"
    return "other"


def _repo_id() -> str:
    if "/" in FW_MODEL_SIZE:
        return FW_MODEL_SIZE
    try:
        from faster_whisper.utils import _MODELS
        return _MODELS[FW_MODEL_SIZE]
    except Exception:
        fallback = {
            "tiny": "Systran/faster-whisper-tiny",
            "base": "Systran/faster-whisper-base",
            "small": "Systran/faster-whisper-small",
            "medium": "Systran/faster-whisper-medium",
            "large-v3": "Systran/faster-whisper-large-v3",
        }
        return fallback[FW_MODEL_SIZE]


def _public_status(backend: str, model: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": backend, "model": model,
        **{key: snapshot[key] for key in (
            "state", "pct", "mb", "total_mb", "reason", "detail", "updated_at"
        )},
    }


def status() -> dict[str, Any]:
    backend = _backend()
    if backend == "cli":
        exists = Path(GGML_MODEL).exists()
        return {
            "backend": "cli", "model": Path(GGML_MODEL).name,
            "state": "ready" if exists else "failed", "pct": 100 if exists else None,
            "mb": round(Path(GGML_MODEL).stat().st_size / 1_000_000) if exists else 0,
            "total_mb": round(Path(GGML_MODEL).stat().st_size / 1_000_000) if exists else 0,
            "reason": None if exists else "other",
            "detail": None if exists else "whisper-cli 모델 파일이 없습니다.",
            "updated_at": _now(),
        }
    with _dl_lock:
        snapshot = dict(_dl)
    if snapshot["state"] == "absent":
        try:
            from faster_whisper.utils import download_model
            download_model(FW_MODEL_SIZE, cache_dir=str(_MODEL_DIR), local_files_only=True)
        except Exception:
            pass
        else:
            with _dl_lock:
                _dl.update(state="ready", pct=100, reason=None, detail=None, updated_at=_now())
                snapshot = dict(_dl)
    return _public_status("faster", FW_MODEL_SIZE, snapshot)


def _faster_status_dict() -> dict[str, Any]:
    with _dl_lock:
        snapshot = dict(_dl)
    if snapshot["state"] == "absent":
        try:
            from faster_whisper.utils import download_model
            download_model(FW_MODEL_SIZE, cache_dir=str(_MODEL_DIR), local_files_only=True)
        except Exception:
            pass
        else:
            with _dl_lock:
                _dl.update(state="ready", pct=100, reason=None, detail=None, updated_at=_now())
                snapshot = dict(_dl)
    return _public_status("faster", FW_MODEL_SIZE, snapshot)


def _faster_state() -> str:
    return _faster_status_dict()["state"]


def prewarm(blocking: bool = False) -> dict[str, Any]:
    if _backend() == "cli":
        return status()
    current = _faster_status_dict()
    if current["state"] == "ready":
        return current
    if current["state"] == "downloading":
        if blocking:
            deadline = time.monotonic() + 20 * 60
            while time.monotonic() < deadline:
                current = _faster_status_dict()
                if current["state"] != "downloading":
                    return current
                time.sleep(1)
            with _dl_lock:
                return _public_status("faster", FW_MODEL_SIZE, dict(_dl))
        return current
    try:
        if _free_bytes() < _MIN_FREE_BYTES:
            with _dl_lock:
                _dl.update(state="failed", reason="disk", detail="모델 저장 공간이 부족합니다.", updated_at=_now())
                return _public_status("faster", FW_MODEL_SIZE, dict(_dl))
    except OSError:
        pass
    with _dl_lock:
        if _dl["state"] == "downloading":
            current = _public_status("faster", FW_MODEL_SIZE, dict(_dl))
            already_downloading = True
        else:
            already_downloading = False
        if already_downloading:
            pass
        else:
            _ProgressTqdm._agg = {"seen": 0.0, "total": 0.0}
            now = _now()
            _dl.update(state="downloading", pct=None, mb=0, total_mb=0, reason=None,
                       detail=None, started_at=now, updated_at=now)
    if already_downloading:
        return current

    def run() -> None:
        stop_poll = threading.Event()

        def poll_partial_files() -> None:
            while not stop_poll.wait(0.5):
                try:
                    partial = sum(p.stat().st_size for p in _MODEL_DIR.rglob("*.incomplete"))
                    with _dl_lock:
                        if _dl["mb"] == 0 and partial:
                            _dl["mb"] = round(partial / 1_000_000)
                            _dl["updated_at"] = _now()
                except OSError:
                    pass

        threading.Thread(target=poll_partial_files, daemon=True).start()
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                _repo_id(), cache_dir=str(_MODEL_DIR),
                allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                                "tokenizer.json", "vocabulary.*"],
                tqdm_class=_ProgressTqdm,
            )
            with _load_lock:
                _fw_model()
            with _dl_lock:
                _dl.update(state="ready", pct=100, reason=None, detail=None, updated_at=_now())
        except Exception as exc:
            with _dl_lock:
                _dl.update(state="failed", reason=_classify(exc), detail=str(exc)[:200], updated_at=_now())
            if blocking:
                raise
        finally:
            stop_poll.set()

    if blocking:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
    return status()


def _cli_available() -> bool:
    return shutil.which(WHISPER_CLI) is not None and Path(GGML_MODEL).exists()


def _backend() -> str:
    b = os.environ.get("WHISPER_BACKEND", "auto")
    if b == "auto":
        return "cli" if _cli_available() else "faster"
    return b


def info() -> dict[str, Any]:
    b = _backend()
    if b == "cli":
        p = Path(GGML_MODEL)
        return {
            "backend": "whisper.cpp (Metal)",
            "model": p.name,
            "model_dir": str(p.parent),
            "downloaded": p.exists(),
            "size_mb": round(p.stat().st_size / 1_000_000) if p.exists() else 0,
            "device": "metal",
            "compute_type": "ggml",
            "state": status()["state"],
        }
    files = (
        [f for f in _MODEL_DIR.rglob("*") if f.is_file() and not f.is_symlink()]
        if _MODEL_DIR.exists()
        else []
    )
    return {
        "backend": "faster-whisper (cpu)",
        "model": FW_MODEL_SIZE,
        "model_dir": str(_MODEL_DIR),
        "downloaded": sum(f.stat().st_size for f in files) > 100_000_000,
        "size_mb": round(sum(f.stat().st_size for f in files) / 1_000_000),
        "device": FW_DEVICE,
        "compute_type": FW_COMPUTE,
        "state": status()["state"],
    }


def _audio_seconds(path: str) -> float:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()), 2)
    except Exception:
        return 0.0


def _to_wav16(src: str) -> str:
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    subprocess.run(
        [FFMPEG, "-y", "-i", src, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav],
        capture_output=True, timeout=600, check=True,
    )
    return wav


def _transcribe_cli(audio_path: str, language: str) -> dict[str, Any]:
    wav = _to_wav16(audio_path)
    out_base = wav[:-4] + "_out"
    try:
        subprocess.run(
            [WHISPER_CLI, "-m", GGML_MODEL, "-f", wav, "-l", language,
             "-t", str(CPU_THREADS), "-np", "-oj", "-of", out_base],
            capture_output=True, timeout=1800, check=True,
        )
        data = json.loads(Path(out_base + ".json").read_text(encoding="utf-8"))
        parts = [
            {
                "start": round(s["offsets"]["from"] / 1000, 2),
                "end": round(s["offsets"]["to"] / 1000, 2),
                "text": s["text"].strip(),
            }
            for s in data.get("transcription", [])
        ]
        return {
            "text": " ".join(p["text"] for p in parts if p["text"]).strip(),
            "segments": parts,
            "language": (data.get("result") or {}).get("language", language),
            "audio_minutes": round(_audio_seconds(audio_path) / 60, 2),
            "model": Path(GGML_MODEL).name,
            "backend": "whisper.cpp",
        }
    finally:
        for f in (wav, out_base + ".json"):
            try:
                os.unlink(f)
            except OSError:
                pass


@lru_cache(maxsize=1)
def _fw_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        FW_MODEL_SIZE, device=FW_DEVICE, compute_type=FW_COMPUTE,
        download_root=str(_MODEL_DIR), cpu_threads=CPU_THREADS,
    )


def _transcribe_faster(audio_path: str, language: str) -> dict[str, Any]:
    state = _faster_state()
    if state != "ready":
        if state == "absent":
            prewarm(blocking=False)
        raise ModelNotReady(_faster_status_dict())
    with _load_lock:
        model = _fw_model()
    segments, meta = model.transcribe(
        audio_path,
        language=language,
        beam_size=FW_BEAM,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    parts = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments
    ]
    return {
        "text": " ".join(p["text"] for p in parts).strip(),
        "segments": parts,
        "language": meta.language,
        "audio_minutes": round((meta.duration or 0) / 60, 2),
        "model": FW_MODEL_SIZE,
        "backend": "faster-whisper",
    }


def transcribe(audio_path: str, language: str = "ko") -> dict[str, Any]:
    if _backend() == "cli":
        try:
            return _transcribe_cli(audio_path, language)
        except Exception:
            # CLI 실패 시 faster-whisper 로 폴백 (한 번 더 시도)
            return _transcribe_faster(audio_path, language)
    return _transcribe_faster(audio_path, language)
