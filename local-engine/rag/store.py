"""
벡터 저장소: 조각(text + page + 메타 + 임베딩)을 SQLite에 넣고 코사인 유사도로 검색한다.

최소 파이프라인이라 전용 벡터 DB 대신 SQLite + 파이썬 브루트포스 코사인을 쓴다.
약관 몇 개 규모에선 충분하고, 나중에 rag/ 안에서 갈아끼우기 쉽다.

DB 경로 우선순위: 인자 db_path > 환경변수 RAG_DB_PATH > rag/data/index.sqlite3
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

_PACKAGED_DEFAULT = Path(__file__).parent / "data" / "index.sqlite3"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.environ.get("RAG_DB_PATH") or _PACKAGED_DEFAULT)


class VectorStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = resolve_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id    TEXT PRIMARY KEY,
                doc_id      TEXT NOT NULL,
                filename    TEXT,
                page        INTEGER NOT NULL,
                text        TEXT NOT NULL,
                embedding   TEXT NOT NULL,
                embed_model TEXT
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
        self.conn.commit()

    def clear(self, doc_id: str | None = None) -> int:
        cur = (
            self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            if doc_id
            else self.conn.execute("DELETE FROM chunks")
        )
        self.conn.commit()
        return cur.rowcount

    def add(self, chunks: list, embeddings: list[list[float]], embed_model: str) -> int:
        rows = [
            (c.chunk_id, c.doc_id, c.filename, c.page, c.text, json.dumps(e), embed_model)
            for c, e in zip(chunks, embeddings)
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO chunks "
            "(chunk_id, doc_id, filename, page, text, embedding, embed_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count(self, doc_id: str | None = None) -> int:
        if doc_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return row[0]

    def docs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT doc_id, filename, COUNT(*), MIN(embed_model) "
            "FROM chunks GROUP BY doc_id, filename ORDER BY filename"
        ).fetchall()
        return [
            {"doc_id": d, "filename": f, "chunks": n, "embed_model": m}
            for d, f, n, m in rows
        ]

    def search(
        self,
        query_emb: list[float],
        top_k: int = 5,
        doc_id: str | None = None,
        doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        q = "SELECT chunk_id, doc_id, filename, page, text, embedding FROM chunks"
        params: tuple = ()
        scope = list(doc_ids) if doc_ids else ([doc_id] if doc_id else [])
        if scope:
            q += f" WHERE doc_id IN ({', '.join('?' for _ in scope)})"
            params = tuple(scope)

        qn = math.sqrt(sum(x * x for x in query_emb)) or 1.0
        scored: list[dict[str, Any]] = []
        for chunk_id, d, f, page, text, emb_json in self.conn.execute(q, params):
            emb = json.loads(emb_json)
            dot = sum(a * b for a, b in zip(query_emb, emb))
            en = math.sqrt(sum(x * x for x in emb)) or 1.0
            scored.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": d,
                    "filename": f,
                    "page": page,
                    "text": text,
                    "score": round(dot / (qn * en), 4),
                }
            )
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    def close(self) -> None:
        self.conn.close()
