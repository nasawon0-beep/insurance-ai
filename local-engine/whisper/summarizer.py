"""
전사 텍스트 → 상담 이력 요약 (qwen2.5). 통화/미팅 녹취를 상담 기록 한 건으로 정리한다.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("INTAKE_LLM_MODEL", "qwen2.5:7b")  # 요약은 작은 모델로 충분
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")

_PROMPT = """다음은 보험 상담자와 고객의 통화/미팅 녹취를 전사한 것이다.
상담 이력으로 남길 수 있게 아래 JSON 하나만 출력하라. 없는 값은 null 또는 빈 배열.
전사에 없는 내용은 지어내지 마라.

{"title": "한 줄 제목 (20자 내외)",
 "summary": "3~5문장 요약",
 "key_points": ["핵심 내용", "..."],
 "customer_interests": ["고객이 관심 보인 상품/보장", "..."],
 "action_items": ["상담자가 후속으로 할 일", "..."],
 "follow_up_at": "다음 연락 예정일 YYYY-MM-DD (언급 없으면 null)"}

[녹취 전사]
"""


def summarize(transcript: str, model: Optional[str] = None) -> dict[str, Any]:
    model = model or LLM_MODEL
    payload = {
        "model": model,
        "prompt": _PROMPT + transcript.strip(),
        "stream": False,
        "format": "json",
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_predict": 800, "num_ctx": 4096},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        out = json.loads((data.get("response") or "").strip())
    except json.JSONDecodeError:
        out = {}

    return {
        "title": out.get("title") or "녹취 상담",
        "summary": out.get("summary") or transcript[:500],
        "key_points": out.get("key_points") or [],
        "customer_interests": out.get("customer_interests") or [],
        "action_items": out.get("action_items") or [],
        "follow_up_at": out.get("follow_up_at") or None,
    }
