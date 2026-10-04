"""Ollama/AMD GPU 진단 유틸리티.

Windows 파일럿 PC에서 Ollama가 GPU를 쓰는지 확인하고, CPU 폴백 시
권장 설정을 health/script 양쪽에서 같은 기준으로 보여준다.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.request
from typing import Any

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
RECOMMENDED_DEFAULT_MODEL = "qwen2.5:7b"
OPTIONAL_LARGE_MODEL = "qwen2.5:14b"


def _fetch_json(path: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(f"{OLLAMA_BASE}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cpu_threads() -> int:
    raw = os.environ.get("OLLAMA_NUM_THREAD")
    if raw:
        if raw.lower() == "auto":
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return max(1, os.cpu_count() or 1)


def ollama_runtime_status() -> dict[str, Any]:
    """Return current Ollama model/runtime state without raising."""
    status: dict[str, Any] = {
        "base_url": OLLAMA_BASE,
        "cpu_count": os.cpu_count() or 1,
        "num_thread": _cpu_threads(),
        "num_thread_source": "OLLAMA_NUM_THREAD" if os.environ.get("OLLAMA_NUM_THREAD") else "cpu_count",
        "hsa_override_gfx_version": os.environ.get("HSA_OVERRIDE_GFX_VERSION"),
        "hsa_override_gfx_version_0": os.environ.get("HSA_OVERRIDE_GFX_VERSION_0"),
        "hsa_override_gfx_version_1": os.environ.get("HSA_OVERRIDE_GFX_VERSION_1"),
    }
    try:
        version = _fetch_json("/api/version")
        status["version"] = version.get("version")
        status["connected"] = True
    except Exception as exc:
        status["connected"] = False
        status["error"] = str(exc)
        return status

    try:
        ps = _fetch_json("/api/ps")
        loaded = []
        for model in ps.get("models", []):
            size = int(model.get("size") or 0)
            size_vram = int(model.get("size_vram") or 0)
            processor = "gpu" if size_vram > 0 else "cpu"
            if size and 0 < size_vram < size:
                processor = "mixed"
            loaded.append(
                {
                    "name": model.get("name") or model.get("model"),
                    "processor": processor,
                    "size": size,
                    "size_vram": size_vram,
                    "context_length": model.get("context_length"),
                    "quantization_level": (model.get("details") or {}).get("quantization_level"),
                    "parameter_size": (model.get("details") or {}).get("parameter_size"),
                }
            )
        status["loaded_models"] = loaded
        status["gpu_active"] = any(m["processor"] in ("gpu", "mixed") for m in loaded)
    except Exception as exc:
        status["ps_error"] = str(exc)
        status["loaded_models"] = []
        status["gpu_active"] = False

    try:
        tags = _fetch_json("/api/tags")
        installed = [m.get("name") for m in tags.get("models", []) if m.get("name")]
        status["installed_models"] = installed
        status["optional_14b_installed"] = OPTIONAL_LARGE_MODEL in installed
        status["default_model_installed"] = RECOMMENDED_DEFAULT_MODEL in installed
    except Exception as exc:
        status["tags_error"] = str(exc)

    return status


def _run_windows_probe(command: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=8,
            errors="replace",
        )
    except Exception:
        return None
    output = (proc.stdout or proc.stderr or "").strip()
    return output or None


def windows_gpu_inventory() -> dict[str, Any]:
    """Best-effort Windows GPU inventory for diagnostics script/health."""
    if platform.system().lower() != "windows":
        return {"platform": platform.system(), "gpus": []}

    # PowerShell is preferred because WMIC is removed on newer Windows 11 builds.
    ps = _run_windows_probe(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json -Compress",
        ]
    )
    if ps:
        try:
            data = json.loads(ps)
            if isinstance(data, dict):
                data = [data]
            return {"platform": "Windows", "gpus": data, "source": "Get-CimInstance"}
        except json.JSONDecodeError:
            return {"platform": "Windows", "raw": ps, "source": "Get-CimInstance"}

    wmic = _run_windows_probe(
        ["cmd", "/c", "wmic path win32_VideoController get name,driverversion,adapterram /format:list"]
    )
    return {"platform": "Windows", "raw": wmic or "unavailable", "source": "wmic"}


def optimization_recommendations(status: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    if not status.get("connected"):
        return ["Ollama가 연결되지 않았습니다. Ollama 실행 후 다시 확인하세요."]

    version = status.get("version") or "unknown"
    recs.append(f"Ollama {version} 사용 중입니다. AMD Windows GPU는 ROCm/HIP7 또는 Vulkan 지원 빌드가 필요합니다.")

    if status.get("gpu_active"):
        recs.append("현재 Ollama /api/ps 기준 GPU 또는 mixed 모드가 활성입니다.")
    else:
        recs.append("현재 Ollama /api/ps 기준 size_vram=0 입니다. CPU 전용으로 실행 중입니다.")
        recs.append("AMD gfx1103에서 공식 빌드가 CPU로 폴백하면 HSA_OVERRIDE_GFX_VERSION=11.0.0 또는 11.0.3은 진단용으로만 시도하세요. override만으로 안 되면 gfx1103 포함 ROCm/Vulkan 지원 빌드가 필요합니다.")

    threads = status.get("num_thread")
    cpu_count = status.get("cpu_count")
    if threads == 0:
        recs.append("OLLAMA_NUM_THREAD=auto: Ollama 기본값에 맡깁니다.")
    else:
        recs.append(f"CPU 폴백 최적화: 현재 num_thread={threads}. 이 PC 논리 코어({cpu_count}) 기준으로 과열/버벅임이 있으면 6~8, 최대 속도 우선이면 논리 코어 수를 사용하세요.")

    if status.get("optional_14b_installed"):
        recs.append("qwen2.5:14b는 기본 사용하지 않습니다. 품질 검증이 필요한 경우 RAG_LLM_MODEL=qwen2.5:14b로 명시한 때만 사용하세요.")
    else:
        recs.append("qwen2.5:14b는 설치되어 있지 않습니다. 기본 7B 정책 유지가 적합합니다.")
    recs.append("현재 7B 모델은 Q4_K_M 양자화입니다. CPU 전용에서는 7B Q4_K_M 유지, 더 빠른 응답은 qwen2.5:3b 또는 1.5b를 환경변수로 선택하세요.")
    return recs


def full_diagnostics() -> dict[str, Any]:
    status = ollama_runtime_status()
    return {
        "ollama": status,
        "windows_gpu": windows_gpu_inventory(),
        "recommendations": optimization_recommendations(status),
    }
