"""
임베더: 텍스트 → 의미 벡터 (작업 E, 2단계).

- 기본은 Ollama의 BGE-M3 (아키텍처 2번). `ollama pull bge-m3` 되어 있으면 자동 사용.
- 없으면 HashingEmbedder 폴백으로 내려간다. 모델 없이도 파이프라인이 돌고
  대략적인 키워드 매칭 확인은 된다 (정확도는 BGE-M3보다 낮음).
- get_embedder()가 상황을 보고 알아서 고른다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "bge-m3")
EMBED_KEEP_ALIVE = os.environ.get(
    "OLLAMA_EMBED_KEEP_ALIVE",
    os.environ.get("OLLAMA_KEEP_ALIVE", "2m"),
)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class HashingEmbedder:
    """모델 없이 도는 폴백. 해싱 트릭 + tf 가중 + L2 정규화.

    hash()는 프로세스마다 값이 바뀌므로(재시작 시 인덱스 깨짐) blake2b를 쓴다.
    """

    name = "hashing-fallback"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            vec[self._bucket(tok)] += 1.0
        return _l2_normalize(vec)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OllamaEmbedder:
    def __init__(self, model: str = EMBED_MODEL, base: str = OLLAMA_BASE):
        self.model = model
        self.base = base
        self.name = f"ollama:{model}"

    def _embed_one(self, text: str) -> list[float]:
        payload = json.dumps(
            {"model": self.model, "prompt": text, "keep_alive": EMBED_KEEP_ALIVE}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        emb = data.get("embedding")
        if not emb:
            raise RuntimeError(f"임베딩 응답에 embedding 필드가 없음: {data}")
        return _l2_normalize(emb)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


def _model_available(model: str, base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    names = {m.get("name", "") for m in data.get("models", [])}
    return any(n == model or n.split(":")[0] == model.split(":")[0] for n in names)


def get_embedder():
    """BGE-M3(Ollama)가 있으면 그걸, 없으면 해싱 폴백."""
    if _model_available(EMBED_MODEL, OLLAMA_BASE):
        return OllamaEmbedder()
    return HashingEmbedder()
