"""Transactional, authenticated LAN sync core.

This module deliberately does not bind a socket or import the existing desktop HTTP
application.  ``app.py`` exposes only this narrow protocol.  The core uses no wall
clock for conflict resolution: base/local/remote values decide every merge.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


class AuthError(RuntimeError):
    pass


class ValidationError(RuntimeError):
    pass


class PairingRateLimitError(AuthError):
    def __init__(self, retry_after: int):
        super().__init__("pairing temporarily unavailable")
        self.retry_after = max(1, int(retry_after))


class ConflictKind(str, Enum):
    FIELD = "field"
    DELETE_MODIFY = "delete_modify"


@dataclass(frozen=True)
class PairingCode:
    code: str
    expires_at: int


@dataclass(frozen=True)
class PairedDevice:
    device_id: str
    token: str


@dataclass(frozen=True)
class AuthContext:
    device_id: str


@dataclass(frozen=True)
class PushResult:
    operation_id: str
    status: str
    cursor: int | None = None
    conflict_id: str | None = None


@dataclass(frozen=True)
class PullResult:
    items: list[dict[str, Any]]
    next_cursor: int
    has_more: bool


WRITABLE_FIELDS: dict[str, frozenset[str]] = {
    "customer": frozenset({"phone", "address", "memo"}),
    "consultation": frozenset({
        "customer_id", "consulted_at", "channel", "title", "content",
        "follow_up_at", "follow_up_done_at",
    }),
}
READ_ONLY_TYPES = frozenset({"policy", "coverage_analysis", "policy_coverage"})
PAIRING_WINDOW_SECONDS = 60
PAIRING_SOURCE_FAILURE_LIMIT = 5
PAIRING_SOURCE_COOLDOWN_SECONDS = 30
PAIRING_GLOBAL_FAILURE_LIMIT = 30
PAIRING_GLOBAL_COOLDOWN_SECONDS = 60
PAIRING_MAX_SOURCE_BUCKETS = 256
PAIRING_BUCKET_RETENTION_SECONDS = 600


class SyncStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._pairing_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        self.conn.close()

    def initialize(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sync_devices(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, token_digest TEXT NOT NULL UNIQUE,
          revoked_at INTEGER, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_pairing_codes(
          digest TEXT PRIMARY KEY, salt TEXT NOT NULL, expires_at INTEGER NOT NULL,
          used_at INTEGER, pc_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_entities(
          entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL,
          deleted INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(entity_type, entity_id)
        );
        CREATE TABLE IF NOT EXISTS sync_changes(
          cursor INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL, payload TEXT NOT NULL, deleted INTEGER NOT NULL,
          revision INTEGER NOT NULL, source_device_id TEXT,
          FOREIGN KEY(source_device_id) REFERENCES sync_devices(id)
        );
        CREATE TABLE IF NOT EXISTS sync_operations(
          device_id TEXT NOT NULL, operation_id TEXT NOT NULL, response TEXT NOT NULL,
          PRIMARY KEY(device_id, operation_id),
          FOREIGN KEY(device_id) REFERENCES sync_devices(id)
        );
        CREATE TABLE IF NOT EXISTS sync_conflicts(
          id TEXT PRIMARY KEY, device_id TEXT NOT NULL, entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL, kind TEXT NOT NULL, base TEXT NOT NULL,
          local TEXT NOT NULL, remote TEXT NOT NULL,
          local_deleted INTEGER NOT NULL, remote_deleted INTEGER NOT NULL,
          resolved_at INTEGER, resolution TEXT,
          FOREIGN KEY(device_id) REFERENCES sync_devices(id)
        );
        CREATE TABLE IF NOT EXISTS sync_tombstone_acks(
          device_id TEXT NOT NULL, cursor INTEGER NOT NULL,
          PRIMARY KEY(device_id, cursor),
          FOREIGN KEY(device_id) REFERENCES sync_devices(id),
          FOREIGN KEY(cursor) REFERENCES sync_changes(cursor)
        );
        CREATE TABLE IF NOT EXISTS sync_pairing_rate_limits(
          bucket_type TEXT NOT NULL, bucket_key TEXT NOT NULL,
          window_started_at INTEGER NOT NULL, attempts INTEGER NOT NULL,
          failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY(bucket_type, bucket_key)
        );
        """)

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _pair_digest(code: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), 120_000).hex()

    def create_pairing_code(self, pc_name: str, ttl_seconds: int = 300) -> PairingCode:
        if not pc_name.strip() or ttl_seconds < 1:
            raise ValidationError("invalid pairing request")
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        digest = self._pair_digest(code, salt)
        expires = int(self._clock()) + ttl_seconds
        self.conn.execute(
            "INSERT INTO sync_pairing_codes(digest,salt,expires_at,pc_name) VALUES(?,?,?,?)",
            (digest, salt, expires, pc_name.strip()),
        )
        return PairingCode(code, expires)

    def pair_device(self, code: str, device_name: str, source: str) -> PairedDevice:
        source_bucket = self._begin_pairing_attempt(source)
        now = int(self._clock())
        try:
            clean_name = device_name.strip()
            if not clean_name or len(clean_name) > 128:
                raise AuthError("invalid pairing request")
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                raise AuthError("invalid, expired, or already-used pairing code")
            rows = self.conn.execute(
                "SELECT digest,salt,expires_at,used_at FROM sync_pairing_codes WHERE used_at IS NULL AND expires_at>=?",
                (now,),
            ).fetchall()
            match = None
            for row in rows:
                candidate = self._pair_digest(code, row["salt"])
                if hmac.compare_digest(candidate, row["digest"]):
                    match = row
                    break
            if match is None:
                raise AuthError("invalid, expired, or already-used pairing code")
            device_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            with self.conn:
                updated = self.conn.execute(
                    "UPDATE sync_pairing_codes SET used_at=? WHERE digest=? AND used_at IS NULL",
                    (now, match["digest"]),
                ).rowcount
                if updated != 1:
                    raise AuthError("pairing code already used")
                self.conn.execute(
                    "INSERT INTO sync_devices(id,name,token_digest,created_at) VALUES(?,?,?,?)",
                    (device_id, clean_name, self._token_digest(token), now),
                )
        except Exception:
            self._finish_pairing_attempt(source_bucket, succeeded=False)
            raise
        self._finish_pairing_attempt(source_bucket, succeeded=True)
        return PairedDevice(device_id, token)

    @staticmethod
    def _source_digest(source: str) -> str:
        return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()

    def _begin_pairing_attempt(self, source: str) -> str:
        now = int(self._clock())
        source_digest = self._source_digest(source or "unknown")
        retry_after = 0
        with self._pairing_lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "DELETE FROM sync_pairing_rate_limits WHERE bucket_type='source' AND updated_at<? AND blocked_until<=?",
                    (now - PAIRING_BUCKET_RETENTION_SECONDS, now),
                )
                source_bucket = source_digest
                exists = self.conn.execute(
                    "SELECT 1 FROM sync_pairing_rate_limits WHERE bucket_type='source' AND bucket_key=?",
                    (source_bucket,),
                ).fetchone()
                if exists is None:
                    count = self.conn.execute(
                        "SELECT COUNT(*) FROM sync_pairing_rate_limits WHERE bucket_type='source' AND bucket_key<>'overflow'"
                    ).fetchone()[0]
                    if count >= PAIRING_MAX_SOURCE_BUCKETS:
                        source_bucket = "overflow"
                buckets = [
                    ("global", "all", PAIRING_GLOBAL_FAILURE_LIMIT, PAIRING_GLOBAL_COOLDOWN_SECONDS),
                    ("source", source_bucket, PAIRING_SOURCE_FAILURE_LIMIT, PAIRING_SOURCE_COOLDOWN_SECONDS),
                ]
                loaded = []
                for bucket_type, bucket_key, limit, cooldown in buckets:
                    row = self.conn.execute(
                        "SELECT window_started_at,attempts,failures,blocked_until FROM sync_pairing_rate_limits WHERE bucket_type=? AND bucket_key=?",
                        (bucket_type, bucket_key),
                    ).fetchone()
                    if row is None or now >= int(row["window_started_at"]) + PAIRING_WINDOW_SECONDS or (
                        int(row["blocked_until"]) and now >= int(row["blocked_until"])
                    ):
                        state = {"window_started_at": now, "attempts": 0, "failures": 0, "blocked_until": 0}
                    else:
                        state = dict(row)
                    if int(state["blocked_until"]) > now:
                        retry_after = max(retry_after, int(state["blocked_until"]) - now)
                    elif int(state["attempts"]) >= limit:
                        state["blocked_until"] = now + cooldown
                        retry_after = max(retry_after, cooldown)
                    loaded.append((bucket_type, bucket_key, state))
                for bucket_type, bucket_key, state in loaded:
                    attempts = int(state["attempts"]) + (0 if retry_after else 1)
                    self.conn.execute(
                        "INSERT INTO sync_pairing_rate_limits(bucket_type,bucket_key,window_started_at,attempts,failures,blocked_until,updated_at) "
                        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(bucket_type,bucket_key) DO UPDATE SET "
                        "window_started_at=excluded.window_started_at,attempts=excluded.attempts,failures=excluded.failures,"
                        "blocked_until=excluded.blocked_until,updated_at=excluded.updated_at",
                        (bucket_type, bucket_key, state["window_started_at"], attempts, state["failures"], state["blocked_until"], now),
                    )
                self.conn.execute("COMMIT")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        if retry_after:
            raise PairingRateLimitError(retry_after)
        return source_bucket

    def _finish_pairing_attempt(self, source_bucket: str, *, succeeded: bool) -> None:
        now = int(self._clock())
        with self._pairing_lock, self.conn:
            if succeeded and source_bucket != "overflow":
                self.conn.execute(
                    "DELETE FROM sync_pairing_rate_limits WHERE bucket_type='source' AND bucket_key=?",
                    (source_bucket,),
                )
                return
            for bucket_type, bucket_key, limit, cooldown in (
                ("global", "all", PAIRING_GLOBAL_FAILURE_LIMIT, PAIRING_GLOBAL_COOLDOWN_SECONDS),
                ("source", source_bucket, PAIRING_SOURCE_FAILURE_LIMIT, PAIRING_SOURCE_COOLDOWN_SECONDS),
            ):
                if succeeded:
                    continue
                row = self.conn.execute(
                    "SELECT failures FROM sync_pairing_rate_limits WHERE bucket_type=? AND bucket_key=?",
                    (bucket_type, bucket_key),
                ).fetchone()
                failures = (int(row["failures"]) if row else 0) + 1
                blocked_until = now + cooldown if failures >= limit else 0
                self.conn.execute(
                    "UPDATE sync_pairing_rate_limits SET failures=?,blocked_until=?,updated_at=? WHERE bucket_type=? AND bucket_key=?",
                    (failures, blocked_until, now, bucket_type, bucket_key),
                )

    def authenticate(self, token: str) -> AuthContext:
        digest = self._token_digest(token)
        row = self.conn.execute(
            "SELECT id,token_digest,revoked_at FROM sync_devices WHERE token_digest=?",
            (digest,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None or not hmac.compare_digest(row["token_digest"], digest):
            raise AuthError("unauthorized device")
        return AuthContext(row["id"])

    def revoke_device(self, device_id: str) -> None:
        if self.conn.execute("UPDATE sync_devices SET revoked_at=? WHERE id=?", (int(time.time()), device_id)).rowcount != 1:
            raise ValidationError("unknown device")

    def seed_synthetic_customer(self, entity_id: str, payload: dict[str, Any]) -> None:
        """Test/adapter import seam. Production adapters must supply already-authorized data."""
        self._upsert_snapshot("customer", entity_id, payload)

    def seed_synthetic_snapshot(self, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
        """Explicit synthetic/demo seam; never call this with customer production data."""
        if entity_type not in {"customer", "consultation", *READ_ONLY_TYPES}:
            raise ValidationError("unsupported synthetic entity type")
        self._upsert_snapshot(entity_type, entity_id, payload)

    def seed_read_only(self, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
        if entity_type not in READ_ONLY_TYPES:
            raise ValidationError("not a read-only type")
        self._upsert_snapshot(entity_type, entity_id, payload)

    def _upsert_snapshot(self, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        current = self.conn.execute(
            "SELECT payload,deleted,revision FROM sync_entities WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchone()
        if current and current["payload"] == data and not current["deleted"]:
            return
        revision = (int(current["revision"]) if current else 0) + 1
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_entities(entity_type,entity_id,payload,deleted,revision) VALUES(?,?,?,0,?) "
                "ON CONFLICT(entity_type,entity_id) DO UPDATE SET payload=excluded.payload,deleted=0,revision=excluded.revision",
                (entity_type, entity_id, data, revision),
            )
            self.conn.execute(
                "INSERT INTO sync_changes(entity_type,entity_id,payload,deleted,revision,source_device_id) VALUES(?,?,?,0,?,NULL)",
                (entity_type, entity_id, data, revision),
            )

    def reconcile_snapshots(
        self,
        snapshots: dict[tuple[str, str], dict[str, Any]],
        managed_types: frozenset[str],
    ) -> None:
        """Atomically mirror canonical rows and emit tombstones for missing rows."""
        owns_transaction = not self.conn.in_transaction
        try:
            if owns_transaction:
                self.conn.execute("BEGIN IMMEDIATE")
            for (entity_type, entity_id), payload in snapshots.items():
                data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                current = self.conn.execute(
                    "SELECT payload,deleted,revision FROM sync_entities WHERE entity_type=? AND entity_id=?",
                    (entity_type, entity_id),
                ).fetchone()
                if current and current["payload"] == data and not current["deleted"]:
                    continue
                revision = (int(current["revision"]) if current else 0) + 1
                self.conn.execute(
                    "INSERT INTO sync_entities(entity_type,entity_id,payload,deleted,revision) VALUES(?,?,?,0,?) "
                    "ON CONFLICT(entity_type,entity_id) DO UPDATE SET payload=excluded.payload,deleted=0,revision=excluded.revision",
                    (entity_type, entity_id, data, revision),
                )
                self.conn.execute(
                    "INSERT INTO sync_changes(entity_type,entity_id,payload,deleted,revision,source_device_id) VALUES(?,?,?,0,?,NULL)",
                    (entity_type, entity_id, data, revision),
                )
            known = set(snapshots)
            placeholders = ",".join("?" for _ in managed_types)
            rows = self.conn.execute(
                f"SELECT entity_type,entity_id,payload,revision FROM sync_entities "
                f"WHERE entity_type IN ({placeholders}) AND deleted=0",
                tuple(sorted(managed_types)),
            ).fetchall()
            for row in rows:
                key = (row["entity_type"], row["entity_id"])
                if key in known:
                    continue
                revision = int(row["revision"]) + 1
                self.conn.execute(
                    "UPDATE sync_entities SET deleted=1,revision=? WHERE entity_type=? AND entity_id=?",
                    (revision, *key),
                )
                self.conn.execute(
                    "INSERT INTO sync_changes(entity_type,entity_id,payload,deleted,revision,source_device_id) VALUES(?,?,?,1,?,NULL)",
                    (row["entity_type"], row["entity_id"], row["payload"], revision),
                )
            if owns_transaction:
                self.conn.execute("COMMIT")
        except Exception:
            if owns_transaction and self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT payload,deleted FROM sync_entities WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchone()
        if row is None or row["deleted"]:
            return {}
        return json.loads(row["payload"])

    def _validate_operation(self, op: dict[str, Any]) -> None:
        required = {"operation_id", "entity_type", "entity_id", "base", "patch", "deleted"}
        if not required.issubset(op) or not op["operation_id"] or not op["entity_id"]:
            raise ValidationError("incomplete operation")
        entity_type = op["entity_type"]
        if entity_type in READ_ONLY_TYPES or entity_type not in WRITABLE_FIELDS:
            raise ValidationError(f"{entity_type} is read-only or unsupported")
        patch = op["patch"]
        if not isinstance(patch, dict) or not isinstance(op["base"], dict):
            raise ValidationError("base and patch must be objects")
        denied = set(patch) - WRITABLE_FIELDS[entity_type]
        if denied:
            raise ValidationError(f"fields are not writable: {sorted(denied)}")
        if entity_type == "consultation" and not op["deleted"]:
            customer_id = patch.get("customer_id") or op["base"].get("customer_id")
            if not customer_id or not self.get_entity("customer", customer_id):
                raise ValidationError("consultation customer does not exist")

    def push(
        self,
        auth: AuthContext,
        operations: Iterable[dict[str, Any]],
        apply_callback: Callable[[dict[str, Any], dict[str, Any], bool], None] | None = None,
        refresh_callback: Callable[[], None] | None = None,
    ) -> list[PushResult]:
        self._ensure_active(auth)
        ops = list(operations)
        if not ops or len(ops) > 500:
            raise ValidationError("operation batch must contain 1..500 items")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if refresh_callback is not None:
                refresh_callback()
            results: list[PushResult] = []
            for op in ops:
                self._validate_operation(op)
                replay = self.conn.execute(
                    "SELECT response FROM sync_operations WHERE device_id=? AND operation_id=?",
                    (auth.device_id, op["operation_id"]),
                ).fetchone()
                if replay:
                    results.append(PushResult(**json.loads(replay["response"])))
                    continue
                result = self._apply(auth, op)
                if result.status == "applied" and apply_callback is not None:
                    applied = self.conn.execute(
                        "SELECT payload,deleted FROM sync_entities WHERE entity_type=? AND entity_id=?",
                        (op["entity_type"], op["entity_id"]),
                    ).fetchone()
                    apply_callback(op, json.loads(applied["payload"]), bool(applied["deleted"]))
                self.conn.execute(
                    "INSERT INTO sync_operations(device_id,operation_id,response) VALUES(?,?,?)",
                    (auth.device_id, op["operation_id"], json.dumps(asdict(result), sort_keys=True)),
                )
                results.append(result)
            self.conn.execute("COMMIT")
            return results
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def _apply(self, auth: AuthContext, op: dict[str, Any]) -> PushResult:
        entity_type, entity_id = op["entity_type"], op["entity_id"]
        row = self.conn.execute(
            "SELECT payload,deleted,revision FROM sync_entities WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchone()
        remote = json.loads(row["payload"]) if row else {}
        remote_deleted = bool(row["deleted"]) if row else False
        revision = int(row["revision"]) if row else 0
        base, patch = op["base"], op["patch"]
        local_deleted = bool(op["deleted"])

        conflicts: dict[str, dict[str, Any]] = {}
        if local_deleted:
            remote_changed = remote_deleted is False and remote != base
            if remote_changed:
                return self._record_conflict(auth, op, ConflictKind.DELETE_MODIFY, remote, remote_deleted)
            merged, deleted = remote, True
        else:
            if remote_deleted:
                return self._record_conflict(auth, op, ConflictKind.DELETE_MODIFY, remote, True)
            merged = dict(remote)
            for field, local_value in patch.items():
                base_value = base.get(field)
                remote_value = remote.get(field)
                if remote_value != base_value and local_value != remote_value:
                    conflicts[field] = {"base": base_value, "local": local_value, "remote": remote_value}
                else:
                    merged[field] = local_value
            if conflicts:
                return self._record_conflict(auth, op, ConflictKind.FIELD, remote, False)
            deleted = False

        revision += 1
        payload = json.dumps(merged, ensure_ascii=False, sort_keys=True)
        self.conn.execute(
            "INSERT INTO sync_entities(entity_type,entity_id,payload,deleted,revision) VALUES(?,?,?,?,?) "
            "ON CONFLICT(entity_type,entity_id) DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,revision=excluded.revision",
            (entity_type, entity_id, payload, int(deleted), revision),
        )
        cursor = self.conn.execute(
            "INSERT INTO sync_changes(entity_type,entity_id,payload,deleted,revision,source_device_id) VALUES(?,?,?,?,?,?) RETURNING cursor",
            (entity_type, entity_id, payload, int(deleted), revision, auth.device_id),
        ).fetchone()[0]
        return PushResult(op["operation_id"], "applied", int(cursor), None)

    def _record_conflict(self, auth: AuthContext, op: dict[str, Any], kind: ConflictKind, remote: dict[str, Any], remote_deleted: bool) -> PushResult:
        conflict_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sync_conflicts(id,device_id,entity_type,entity_id,kind,base,local,remote,local_deleted,remote_deleted) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                conflict_id, auth.device_id, op["entity_type"], op["entity_id"], kind.value,
                json.dumps(op["base"], ensure_ascii=False, sort_keys=True),
                json.dumps(op["patch"], ensure_ascii=False, sort_keys=True),
                json.dumps(remote, ensure_ascii=False, sort_keys=True),
                int(bool(op["deleted"])), int(remote_deleted),
            ),
        )
        return PushResult(op["operation_id"], "conflict", None, conflict_id)

    def list_conflicts(self, auth: AuthContext) -> list[dict[str, Any]]:
        self._ensure_active(auth)
        rows = self.conn.execute(
            "SELECT * FROM sync_conflicts WHERE device_id=? AND resolved_at IS NULL ORDER BY rowid",
            (auth.device_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("base", "local", "remote"):
                item[key] = json.loads(item[key])
            item["local_deleted"] = bool(item["local_deleted"])
            item["remote_deleted"] = bool(item["remote_deleted"])
            result.append(item)
        return result

    def resolve_conflict(
        self,
        auth: AuthContext,
        conflict_id: str,
        resolution: dict[str, Any],
        deleted: bool = False,
        apply_callback: Callable[[dict[str, Any], dict[str, Any], bool], None] | None = None,
        refresh_callback: Callable[[], None] | None = None,
    ) -> int:
        self._ensure_active(auth)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if refresh_callback is not None:
                refresh_callback()
            row = self.conn.execute(
                "SELECT * FROM sync_conflicts WHERE id=? AND device_id=? AND resolved_at IS NULL",
                (conflict_id, auth.device_id),
            ).fetchone()
            if row is None:
                raise ValidationError("unknown conflict")
            allowed = WRITABLE_FIELDS[row["entity_type"]]
            if set(resolution) - allowed:
                raise ValidationError("resolution contains read-only fields")
            current = self.get_entity(row["entity_type"], row["entity_id"])
            if not current and row["remote_deleted"]:
                current = json.loads(row["remote"])
            current.update(resolution)
            revision_row = self.conn.execute(
                "SELECT revision FROM sync_entities WHERE entity_type=? AND entity_id=?",
                (row["entity_type"], row["entity_id"]),
            ).fetchone()
            revision = (revision_row[0] if revision_row else 0) + 1
            payload = json.dumps(current, ensure_ascii=False, sort_keys=True)
            self.conn.execute(
                "INSERT INTO sync_entities(entity_type,entity_id,payload,deleted,revision) VALUES(?,?,?,?,?) "
                "ON CONFLICT(entity_type,entity_id) DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,revision=excluded.revision",
                (row["entity_type"], row["entity_id"], payload, int(deleted), revision),
            )
            if apply_callback is not None:
                apply_callback(
                    {
                        "operation_id": f"resolve:{conflict_id}",
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "base": json.loads(row["base"]),
                        "patch": resolution,
                        "deleted": deleted,
                    },
                    current,
                    deleted,
                )
            cursor = self.conn.execute(
                "INSERT INTO sync_changes(entity_type,entity_id,payload,deleted,revision,source_device_id) VALUES(?,?,?,?,?,?) RETURNING cursor",
                (row["entity_type"], row["entity_id"], payload, int(deleted), revision, auth.device_id),
            ).fetchone()[0]
            self.conn.execute(
                "UPDATE sync_conflicts SET resolved_at=?,resolution=? WHERE id=?",
                (int(time.time()), json.dumps({"fields": resolution, "deleted": deleted}, ensure_ascii=False, sort_keys=True), conflict_id),
            )
            self.conn.execute("COMMIT")
            return int(cursor)
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def pull(self, auth: AuthContext, cursor: int, limit: int = 200) -> PullResult:
        self._ensure_active(auth)
        if cursor < 0 or limit < 1 or limit > 500:
            raise ValidationError("invalid cursor or limit")
        rows = self.conn.execute(
            "SELECT cursor,entity_type,entity_id,payload,deleted,revision FROM sync_changes WHERE cursor>? ORDER BY cursor LIMIT ?",
            (cursor, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [{
            "cursor": row["cursor"], "entity_type": row["entity_type"], "entity_id": row["entity_id"],
            "payload": json.loads(row["payload"]), "deleted": bool(row["deleted"]), "revision": row["revision"],
        } for row in rows]
        next_cursor = int(rows[-1]["cursor"]) if rows else cursor
        return PullResult(items, next_cursor, has_more)

    def ack_tombstones(self, auth: AuthContext, cursors: Iterable[int]) -> None:
        self._ensure_active(auth)
        values = list(dict.fromkeys(int(c) for c in cursors))
        with self.conn:
            for cursor in values:
                row = self.conn.execute("SELECT deleted FROM sync_changes WHERE cursor=?", (cursor,)).fetchone()
                if row is None or not row["deleted"]:
                    raise ValidationError("ack target is not a tombstone")
                self.conn.execute(
                    "INSERT OR IGNORE INTO sync_tombstone_acks(device_id,cursor) VALUES(?,?)",
                    (auth.device_id, cursor),
                )

    def pending_tombstones(self, auth: AuthContext) -> list[int]:
        self._ensure_active(auth)
        return [int(r[0]) for r in self.conn.execute(
            "SELECT c.cursor FROM sync_changes c LEFT JOIN sync_tombstone_acks a ON a.cursor=c.cursor AND a.device_id=? WHERE c.deleted=1 AND a.cursor IS NULL ORDER BY c.cursor",
            (auth.device_id,),
        )]

    def current_cursor(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(cursor),0) FROM sync_changes").fetchone()[0])

    def operation_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM sync_operations").fetchone()[0])

    def _ensure_active(self, auth: AuthContext) -> None:
        row = self.conn.execute("SELECT revoked_at FROM sync_devices WHERE id=?", (auth.device_id,)).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AuthError("unauthorized device")
