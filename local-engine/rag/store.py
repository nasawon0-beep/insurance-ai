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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PACKAGED_DEFAULT = Path(__file__).parent / "data" / "index.sqlite3"


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.environ.get("RAG_DB_PATH") or _PACKAGED_DEFAULT)


def convert_legacy_store(source, destination, *, cipher, owner, confirmed):
    """Approved conversion to a NEW encrypted file; never modify/delete original.
    Plaintext snapshot exists only in SQLite memory, not a new temporary disk DB.
    """
    import secrets
    from types import SimpleNamespace
    source=Path(source);destination=Path(destination)
    if not confirmed or source.resolve()==destination.resolve():
        raise ValueError('Explicit separate-destination RAG conversion required')
    if destination.exists():raise FileExistsError(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    pending=destination.with_name(destination.name+'.pending-'+secrets.token_hex(12))
    original=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
    memory=sqlite3.connect(':memory:');output=None
    try:
        original.backup(memory)
        tables={r[0] for r in memory.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables.issubset({'chunks','docs'}) or 'chunks' not in tables:
            raise ValueError('Unknown legacy RAG schema requires review')
        output=VectorStore(pending,cipher=cipher,owner=owner)
        for row in memory.execute('SELECT chunk_id,doc_id,filename,page,text,embedding,embed_model FROM chunks'):
            cid,d,name,page,text,embedding,model=row
            encrypted=isinstance(text,str) and text.startswith('enc:v2:')
            if encrypted:
                name=output._name(name,d)
                text=cipher.decrypt_bound(text,output._aad('text',d,cid,page))
                embedding=cipher.decrypt_bound(embedding,output._aad('embedding',d,cid,page))
            elif isinstance(text,str) and text.startswith('enc:'):
                raise ValueError('Unsupported RAG encryption version')
            output.add([SimpleNamespace(chunk_id=cid,doc_id=d,filename=name,page=page,text=text)],[json.loads(embedding)],model)
        if 'docs' in tables:
            for d,h,name,n,model in memory.execute('SELECT doc_id,file_hash,filename,chunks,embed_model FROM docs'):
                if isinstance(name,str) and name.startswith('enc:v2:'):name=output._name(name,d)
                output.upsert_doc(d,h,name,n,model)
        output.close();output=None
        os.link(pending,destination)
    finally:
        if output is not None:output.close()
        memory.close();original.close()
        if pending.exists():pending.unlink()


class VectorStore:
    def __init__(self, db_path: str | Path | None = None, *, cipher=None, owner=None):
        from database.crypto import get_cipher
        from protected_business_key import environment_identity
        self.owner = owner if owner is not None else (environment_identity() or [])
        self.db_path = resolve_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        try:
            populated = False
            if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'").fetchone():
                populated = bool(self.conn.execute('SELECT 1 FROM chunks LIMIT 1').fetchone())
                if self.conn.execute("SELECT 1 FROM chunks WHERE text NOT LIKE 'enc:v2:%' LIMIT 1").fetchone():
                    raise ValueError('Legacy plaintext RAG requires explicit source-preserving conversion')
            self.cipher = cipher or get_cipher(create_if_missing=not populated)
        except Exception:
            self.conn.close()
            raise
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
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS docs (
                doc_id      TEXT PRIMARY KEY,
                file_hash   TEXT NOT NULL,
                filename    TEXT,
                chunks      INTEGER NOT NULL DEFAULT 0,
                embed_model TEXT,
                indexed_at  TEXT NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_hash ON docs(file_hash)")
        self.conn.commit()

    def _aad(self, kind, doc_id, chunk_id='', page=0):
        return json.dumps(['moadoom-rag-v1',self.owner,kind,doc_id,chunk_id,page],separators=(',',':')).encode()

    def _name(self, value, doc_id, write=False):
        method = self.cipher.encrypt_bound if write else self.cipher.decrypt_bound
        return method(value, self._aad('filename',doc_id))

    def clear(self, doc_id: str | None = None) -> int:
        cur = (
            self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            if doc_id
            else self.conn.execute("DELETE FROM chunks")
        )
        if doc_id:
            self.conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
        else:
            self.conn.execute("DELETE FROM docs")
        self.conn.commit()
        return cur.rowcount

    def add(self, chunks: list, embeddings: list[list[float]], embed_model: str) -> int:
        rows = [
            (c.chunk_id, c.doc_id, self._name(c.filename,c.doc_id,True), c.page,
             self.cipher.encrypt_bound(c.text,self._aad('text',c.doc_id,c.chunk_id,c.page)),
             self.cipher.encrypt_bound(json.dumps(e),self._aad('embedding',c.doc_id,c.chunk_id,c.page)), embed_model)
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

    def upsert_doc(
        self,
        doc_id: str,
        file_hash: str,
        filename: str | None,
        chunks: int,
        embed_model: str | None,
    ) -> None:
        indexed_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO docs (doc_id, file_hash, filename, chunks, embed_model, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                file_hash = excluded.file_hash,
                filename = excluded.filename,
                chunks = excluded.chunks,
                embed_model = excluded.embed_model,
                indexed_at = excluded.indexed_at
            """,
            (doc_id, file_hash, self._name(filename,doc_id,True), chunks, embed_model, indexed_at),
        )
        self.conn.commit()

    def doc_info(self, doc_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT doc_id, file_hash, filename, chunks, embed_model, indexed_at FROM docs WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row:
            return {
                "doc_id": row[0],
                "file_hash": row[1],
                "filename": self._name(row[2],row[0]),
                "chunks": row[3],
                "embed_model": row[4],
                "indexed_at": row[5],
            }

        legacy = self.conn.execute(
            "SELECT doc_id, MIN(filename), COUNT(*), MIN(embed_model) FROM chunks WHERE doc_id = ? GROUP BY doc_id",
            (doc_id,),
        ).fetchone()
        if not legacy:
            return None
        return {
            "doc_id": legacy[0],
            "file_hash": legacy[0],
            "filename": self._name(legacy[1],legacy[0]),
            "chunks": legacy[2],
            "embed_model": legacy[3],
            "indexed_at": None,
        }

    def is_same_hash_indexed(self, doc_id: str, file_hash: str) -> bool:
        info = self.doc_info(doc_id)
        return bool(info and info.get("file_hash") == file_hash)

    def count(self, doc_id: str | None = None) -> int:
        if doc_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return row[0]

    def docs(self) -> list[dict[str, Any]]:
        doc_rows = self.conn.execute(
            """
            SELECT d.doc_id, d.file_hash, d.filename, d.chunks, d.embed_model, d.indexed_at,
                   COUNT(c.chunk_id) AS actual_chunks
            FROM docs d
            LEFT JOIN chunks c ON c.doc_id = d.doc_id
            GROUP BY d.doc_id, d.file_hash, d.filename, d.chunks, d.embed_model, d.indexed_at
            ORDER BY d.filename
            """
        ).fetchall()
        docs = [
            {
                "doc_id": d,
                "file_hash": h,
                "filename": self._name(f,d),
                "chunks": actual_n if actual_n is not None else n,
                "embed_model": m,
                "indexed_at": indexed_at,
            }
            for d, h, f, n, m, indexed_at, actual_n in doc_rows
        ]

        legacy_rows = self.conn.execute(
            """
            SELECT c.doc_id, MIN(c.filename), COUNT(*), MIN(c.embed_model)
            FROM chunks c
            LEFT JOIN docs d ON d.doc_id = c.doc_id
            WHERE d.doc_id IS NULL
            GROUP BY c.doc_id
            ORDER BY c.filename
            """
        ).fetchall()
        docs.extend(
            {
                "doc_id": d,
                "file_hash": d,
                "filename": self._name(f,d),
                "chunks": n,
                "embed_model": m,
                "indexed_at": None,
            }
            for d, f, n, m in legacy_rows
        )
        return sorted(docs, key=lambda row: row['filename'] or '')

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
            emb = json.loads(self.cipher.decrypt_bound(emb_json,self._aad('embedding',d,chunk_id,page)))
            dot = sum(a * b for a, b in zip(query_emb, emb))
            en = math.sqrt(sum(x * x for x in emb)) or 1.0
            scored.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": d,
                    "filename": self._name(f,d),
                    "page": page,
                    "text": self.cipher.decrypt_bound(text,self._aad('text',d,chunk_id,page)),
                    "score": round(dot / (qn * en), 4),
                }
            )
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    def close(self) -> None:
        self.conn.close()
