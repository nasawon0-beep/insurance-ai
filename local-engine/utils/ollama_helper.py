"""Ollama 설정 및 모델 선택 헬퍼."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_PREFERRED_MODEL = "qwen2.5:3b"
DEFAULT_FALLBACK_MODEL = "qwen2.5:7b"


class OllamaModelError(RuntimeError):
    """Ollama 모델 선택 실패."""


def get_ollama_base_url() -> str:
    """환경변수 기반 Ollama base URL."""
    return os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).rstrip("/")


@lru_cache(maxsize=8)
def list_ollama_models(base_url: str | None = None, timeout: float = 2.0) -> tuple[str, ...]:
    """Ollama에 설치된 모델명 목록을 반환한다. 서버 미응답이면 빈 튜플."""
    base = (base_url or get_ollama_base_url()).rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return ()

    models: list[str] = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            models.append(name.strip())
    return tuple(models)


def get_ollama_model(
    preferred: str = DEFAULT_PREFERRED_MODEL,
    fallback: str = DEFAULT_FALLBACK_MODEL,
    *,
    base_url: str | None = None,
) -> str:
    """preferred가 있으면 사용하고, 없으면 fallback을 선택한다.

    Ollama 서버가 꺼져 있거나 목록 조회가 실패하면 초기 설치 단순성을 위해
    fallback 모델명을 반환한다. 서버가 응답했는데 두 모델이 모두 없으면
    명확한 에러를 발생시킨다.
    """
    models = list_ollama_models(base_url)
    if not models:
        return fallback
    if preferred in models:
        return preferred
    if fallback in models:
        return fallback
    available = ", ".join(models) or "없음"
    raise OllamaModelError(
        f"Ollama 모델이 없습니다. '{preferred}' 또는 '{fallback}' 모델을 설치하세요. "
        f"현재 설치 모델: {available}"
    )


def get_configured_ollama_model(
    env_var: str = "OLLAMA_MODEL",
    preferred: str = DEFAULT_PREFERRED_MODEL,
    fallback: str = DEFAULT_FALLBACK_MODEL,
    *,
    base_url: str | None = None,
) -> str:
    """환경변수 모델을 우선하고, 없으면 preferred→fallback 순서로 선택."""
    configured = os.environ.get(env_var) or os.environ.get("OLLAMA_MODEL")
    if configured and configured.strip():
        return configured.strip()
    return get_ollama_model(preferred=preferred, fallback=fallback, base_url=base_url)
