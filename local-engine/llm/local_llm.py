"""
Korean local LLM integration for coverage explanations.

This module is deliberately optional: coverage calculation must keep working when
llama-cpp-python or the GGUF model is not installed.  The LLM only writes the
customer-facing rationale sentence; all amounts/status values come from the
existing deterministic Python coverage analyzer.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL_RELATIVE_PATH = "./models/korean-bllossom-3b-q4.gguf"
MAX_CONTEXT_TOKENS = 4096
DEFAULT_MAX_TOKENS = 96

_LLM_LOCK = threading.Lock()
_LLM_INSTANCE: Any | None = None
_LLM_MODEL_PATH: str | None = None
_LLM_LOAD_ERROR: str | None = None


class LocalLLMUnavailable(RuntimeError):
    """Raised when the optional local LLM cannot be used."""


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "n", ""}


def is_enabled() -> bool:
    """Return True when local LLM use is enabled by environment/settings."""
    return _env_flag("USE_LOCAL_LLM", True) and _env_flag("LOCAL_LLM_ENABLED", True)


def resolve_model_path() -> Path:
    """Resolve the GGUF model path.

    LOCAL_LLM_MODEL_PATH defaults to ./models/korean-bllossom-3b-q4.gguf.
    Relative paths are resolved from local-engine/ so packaged and development
    runs do not depend on the shell current directory.
    """
    raw = os.environ.get("LOCAL_LLM_MODEL_PATH", DEFAULT_MODEL_RELATIVE_PATH)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[1] / path


def model_status() -> dict[str, Any]:
    path = resolve_model_path()
    return {
        "enabled": is_enabled(),
        "model_path": str(path),
        "exists": path.exists(),
        "loaded": _LLM_INSTANCE is not None and _LLM_MODEL_PATH == str(path),
        "load_error": _LLM_LOAD_ERROR,
    }


def _gpu_layers() -> int:
    raw = os.environ.get("LOCAL_LLM_N_GPU_LAYERS")
    if raw is None or raw.strip() == "":
        # llama.cpp uses -1 to offload all possible layers when the wheel was
        # built with CUDA/Vulkan/Metal support.  CPU-only wheels safely ignore it
        # by failing at load time on some builds, then we retry with 0 below.
        return -1
    try:
        return int(raw)
    except ValueError:
        return -1


def _load_llama(path: Path, n_gpu_layers: int):
    try:
        from llama_cpp import Llama
    except Exception as exc:  # pragma: no cover - optional dependency
        raise LocalLLMUnavailable("llama-cpp-python is not installed") from exc

    return Llama(
        model_path=str(path),
        n_ctx=int(os.environ.get("LOCAL_LLM_N_CTX", MAX_CONTEXT_TOKENS)),
        n_gpu_layers=n_gpu_layers,
        verbose=_env_flag("LOCAL_LLM_VERBOSE", False),
    )


def get_llm():
    """Return a lazily loaded llama.cpp instance, with CPU fallback."""
    global _LLM_INSTANCE, _LLM_MODEL_PATH, _LLM_LOAD_ERROR

    if not is_enabled():
        raise LocalLLMUnavailable("local LLM is disabled")

    path = resolve_model_path()
    if not path.exists():
        raise LocalLLMUnavailable(f"model file not found: {path}")

    with _LLM_LOCK:
        if _LLM_INSTANCE is not None and _LLM_MODEL_PATH == str(path):
            return _LLM_INSTANCE

        try:
            _LLM_INSTANCE = _load_llama(path, _gpu_layers())
            _LLM_MODEL_PATH = str(path)
            _LLM_LOAD_ERROR = None
            return _LLM_INSTANCE
        except Exception as gpu_exc:
            logger.warning("local LLM GPU/offload load failed; retrying CPU: %s", gpu_exc)
            try:
                _LLM_INSTANCE = _load_llama(path, 0)
                _LLM_MODEL_PATH = str(path)
                _LLM_LOAD_ERROR = None
                return _LLM_INSTANCE
            except Exception as cpu_exc:
                _LLM_INSTANCE = None
                _LLM_MODEL_PATH = None
                _LLM_LOAD_ERROR = str(cpu_exc)
                raise LocalLLMUnavailable(str(cpu_exc)) from cpu_exc


def _format_won(amount: int) -> str:
    try:
        value = int(amount or 0)
    except (TypeError, ValueError):
        value = 0
    if value >= 10_000 and value % 10_000 == 0:
        manwon = value // 10_000
        if manwon >= 10_000 and manwon % 10_000 == 0:
            return f"{manwon // 10_000:,}억원"
        return f"{manwon:,}만원"
    return f"{value:,}원"


def _clean_one_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.strip('"\'` -')
    if not text:
        return ""
    # Keep the first complete sentence to prevent verbose model output.
    m = re.search(r"(.+?[.!?。]|.+?다\.)", text)
    if m:
        text = m.group(1).strip()
    text = text.split("\n", 1)[0].strip()
    if text and text[-1] not in ".!?。":
        text += "."
    return text


def _evidence_reason(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") or []
    snippets: list[str] = []
    for ev in evidence[:2]:
        raw = str(ev.get("text") or ev.get("content") or "")
        raw = re.sub(r"\s+", " ", raw).strip()
        if raw:
            snippets.append(raw[:140])
    if snippets:
        return " ".join(snippets)
    memo = str(item.get("internal_memo") or "").strip()
    if memo:
        return memo
    return f"{item.get('coverage_name', '해당 보장')}의 권장 기준과 현재 가입금액을 비교했습니다."


def _deterministic_prefix(item: dict[str, Any]) -> str:
    name = str(item.get("coverage_name") or "해당 보장")
    status = str(item.get("status") or "확인필요")
    current = _format_won(int(item.get("current_amount") or 0))
    recommended = _format_won(int(item.get("recommended_amount") or 0))
    if status == "충분":
        relation = "권장 금액 이상으로 준비되어 있습니다"
    elif status == "부족":
        relation = f"권장 금액 {recommended}보다 부족합니다"
    elif status == "미가입":
        relation = f"권장 금액 {recommended} 기준으로 가입이 확인되지 않습니다"
    elif status == "확인필요":
        relation = "가입 여부 또는 가입금액 확인이 필요합니다"
    else:
        relation = f"권장 금액 {recommended}와 비교해 추가 확인이 필요합니다"
    return f"고객님의 {name} 보장은 현재 {current}으로 {relation}."


def fallback_coverage_explanation(item: dict[str, Any]) -> str:
    prefix = _deterministic_prefix(item)
    status = str(item.get("status") or "")
    name = str(item.get("coverage_name") or "해당 보장")
    if status == "충분":
        suffix = "현재 수준을 유지하면서 중복 보장 여부만 점검해 보세요."
    elif status == "부족":
        suffix = "치료비와 생활비 부담을 고려해 부족분 보완을 검토해 보세요."
    elif status == "미가입":
        suffix = f"{name} 관련 위험에 대비하려면 신규 가입 여부를 검토해 보세요."
    else:
        suffix = "증권 원문이나 담보 금액을 한 번 더 확인한 뒤 보완 여부를 판단해 보세요."
    return f"{prefix} {suffix}"


def _prompt_for_item(item: dict[str, Any]) -> str:
    name = str(item.get("coverage_name") or "해당 보장")
    status = str(item.get("status") or "확인필요")
    current = _format_won(int(item.get("current_amount") or 0))
    recommended = _format_won(int(item.get("recommended_amount") or 0))
    reason = _evidence_reason(item)
    return f"""당신은 보험 설계사가 고객에게 보장분석 결과를 쉽게 설명하도록 돕습니다.
숫자, 보장명, 상태는 절대 바꾸지 말고, 과장/공포 표현 없이 한 문장으로만 답하세요.

보장 정보:
- 보장명: {name}
- 현재 보장액: {current}
- 권장 보장액: {recommended}
- 상태: {status}
- 근거: {reason}

고객이 이해하기 쉬운 보완 이유만 한 문장으로 설명하세요:"""


def _generate_reason(llm: Any, item: dict[str, Any]) -> str:
    result = llm(
        _prompt_for_item(item),
        max_tokens=int(os.environ.get("LOCAL_LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        temperature=float(os.environ.get("LOCAL_LLM_TEMPERATURE", "0.2")),
        top_p=float(os.environ.get("LOCAL_LLM_TOP_P", "0.9")),
        stop=["\n", "보장 정보:", "고객:"],
    )
    choices = result.get("choices") or []
    text = choices[0].get("text") if choices else ""
    return _clean_one_sentence(text)


def build_coverage_explanation(item: dict[str, Any]) -> str:
    """Generate one customer explanation, falling back to a template."""
    prefix = _deterministic_prefix(item)
    try:
        llm = get_llm()
        reason = _generate_reason(llm, item)
        if not reason:
            return fallback_coverage_explanation(item)
        # The prefix carries deterministic numbers/status.  The LLM contributes
        # only the natural-language rationale, so it cannot alter calculations.
        if reason.startswith("고객님의"):
            return _clean_one_sentence(reason) or fallback_coverage_explanation(item)
        return f"{prefix} {reason}"
    except Exception as exc:
        logger.info("local LLM explanation fallback: %s", exc)
        return fallback_coverage_explanation(item)


def generate_coverage_explanations(items: list[dict[str, Any]]) -> list[str]:
    """Generate explanations for coverage items in order.

    On any model/dependency failure, returns deterministic template output for
    all remaining items.
    """
    explanations: list[str] = []
    if not is_enabled():
        return [fallback_coverage_explanation(item) for item in items]

    try:
        llm = get_llm()
    except Exception as exc:
        logger.info("local LLM unavailable; using templates: %s", exc)
        return [fallback_coverage_explanation(item) for item in items]

    for item in items:
        try:
            reason = _generate_reason(llm, item)
            prefix = _deterministic_prefix(item)
            explanations.append(f"{prefix} {reason}" if reason and not reason.startswith("고객님의") else (reason or fallback_coverage_explanation(item)))
        except Exception as exc:
            logger.info("local LLM item fallback (%s): %s", item.get("coverage_name"), exc)
            explanations.append(fallback_coverage_explanation(item))
    return explanations
