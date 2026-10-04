"""
Local Engine - 최소 헬스체크 서버 (작업 C)

역할: Desktop 앱이 127.0.0.1로 이 서버를 호출해서
     "Ollama가 살아있는지 / 어떤 모델이 준비됐는지"를 확인한다.

이후 whisper/ 등이 여기에 라우터로 붙게 된다.
parser/ (POST /parse/pdf), rag/ (POST /rag/index, GET /rag/search, GET /rag/ask),
database/ (고객 관리 /customers, /policies, /coverage-analysis) 가 이미 붙었다.
지금은 절대 0.0.0.0으로 열지 않는다 (문서 42번 원칙).
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import getpass
import logging
import secrets
import sys
import tempfile
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import threading
import urllib.request
import json

from parser import router as parser_router
from rag import router as rag_router
from database import coverage, router as customer_router
from database.db import connect
from database.uploads import MAX_AUDIO
from whisper.router import router as whisper_router
from auth import router as auth_router


API_SECRET_HEADER = "X-Insurance-AI-Secret"
logger = logging.getLogger(__name__)

_MAINT_STATUS = {
    "backup": {"state": "pending"}, "usage_prune": {"state": "pending"},
    "audit_prune": {"state": "pending"},
}


def _resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return root / name


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "null",
]


def _create_api_secret() -> str:
    value = secrets.token_urlsafe(32)
    uid = getattr(os, "getuid", lambda: getpass.getuser())()
    path = os.environ.get(
        "INSURANCE_AI_API_SECRET_FILE",
        os.path.join(tempfile.gettempdir(), f"insurance-ai-api-{uid}.secret"),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):  # Unix 전용 — Windows 는 os.open 모드로 충분
            os.fchmod(fd, 0o600)
        os.write(fd, value.encode("utf-8"))
    finally:
        os.close(fd)
    return value


API_SECRET = _create_api_secret()


def _lock_is_free(lock_path: str) -> bool:
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio

    # 보안 경고/릴리스 가드: DEV_SKIP_AUTH
    dev_skip_auth = _guard_dev_skip_auth()
    if dev_skip_auth:
        print("⚠️  경고: DEV_SKIP_AUTH=1 활성화됨. 프로덕션 환경에서 절대 사용 금지!", file=sys.stderr)

    if os.environ.get("ENGINE_WARMUP", "1") == "1":
        threading.Thread(target=_warm_ollama, daemon=True).start()
    threading.Thread(target=_warm_whisper, daemon=True).start()
    _startup_maintenance()

    _lock = os.environ.get("APP_LOCK_FILE", "")
    _watch_task = None
    if _lock:
        print(f"local-engine: watching parent lock {_lock!r}", file=sys.stderr)

        async def _watch() -> None:
            # 부모(앱) 종료 시 stderr 파이프의 read end 가 닫혀 이후 어떤 print 든 블록/EPIPE 된다.
            # 그래서 감지 후엔 I/O 없이 바로 os._exit(0). (심장박동·안내 로그 넣지 말 것)
            while True:
                await asyncio.sleep(2)
                if _lock_is_free(_lock):
                    os._exit(0)

        _watch_task = asyncio.ensure_future(_watch())
    try:
        yield
    finally:
        if _watch_task:
            _watch_task.cancel()


def _guard_dev_skip_auth() -> bool:
    """릴리스/프로덕션 실행에서 DEV_SKIP_AUTH 인증 우회를 차단한다."""
    if os.getenv("DEV_SKIP_AUTH") != "1":
        return False
    hermes_env = (os.getenv("HERMES_ENV") or "").strip().lower()
    release = (os.getenv("RELEASE") or "").strip().lower()
    if hermes_env == "production" or release == "true":
        raise RuntimeError(
            "DEV_SKIP_AUTH=1 is not allowed when HERMES_ENV=production or RELEASE=true"
        )
    return True


def _startup_maintenance() -> None:
    """부팅 훅: DB 마이그레이션, 하루 1회 백업, usage_log 정리."""
    retry_after_migration = False
    try:
        from database import backup as _backup

        res = _backup.startup_backup()
        if res is None and os.environ.get("ENGINE_BACKUP", "1") != "1":
            _MAINT_STATUS["backup"] = {"disabled": True, "at": _utcnow_iso()}
        elif res is None:
            _MAINT_STATUS["backup"] = {"state": "skipped", "at": _utcnow_iso()}
        elif isinstance(res, dict) and res.get("created") is False:
            _MAINT_STATUS["backup"] = {
                "state": "skipped", "at": _utcnow_iso(), "reason": res.get("reason")
            }
            if res.get("reason") == "no source db":
                retry_after_migration = True
        else:
            _MAINT_STATUS["backup"] = {
                "state": "ok", "at": _utcnow_iso(), "file": res.get("filename")
            }
    except Exception as e:
        logger.exception("startup backup failed")
        _MAINT_STATUS["backup"] = {
            "state": "error", "at": _utcnow_iso(), "error": str(e)
        }
    from database import usage as _usage
    from database.db import connect, init_schema, _migrate, _migrate_rrn_unique

    conn = connect()
    try:
        # init_schema의 UNIQUE CREATE보다 먼저 legacy 중복을 명확히 진단한다.
        _migrate_rrn_unique(conn)
        init_schema(conn)
        _migrate(conn)
        _migrate_rrn_unique(conn)
        try:
            n = _usage.prune(conn)
            _MAINT_STATUS["usage_prune"] = {
                "state": "ok", "at": _utcnow_iso(), "removed": n
            }
        except Exception as e:
            logger.exception("usage prune failed")
            _MAINT_STATUS["usage_prune"] = {
                "state": "error", "at": _utcnow_iso(), "error": str(e)
            }
        try:
            from database.repo import prune_audit
            try:
                days = int(os.environ.get("AUDIT_RETENTION_DAYS", "730"))
            except (TypeError, ValueError):
                days = 730
            n = prune_audit(conn, days)
            _MAINT_STATUS["audit_prune"] = {
                "state": "ok", "at": _utcnow_iso(), "removed": n
            }
        except Exception as e:
            logger.exception("audit prune failed")
            _MAINT_STATUS["audit_prune"] = {
                "state": "error", "at": _utcnow_iso(), "error": str(e)
            }
    finally:
        conn.close()

    if retry_after_migration:
        try:
            r2 = _backup.startup_backup()
            if isinstance(r2, dict) and r2.get("created"):
                _MAINT_STATUS["backup"] = {
                    "state": "ok", "at": _utcnow_iso(), "file": r2.get("filename")
                }
        except Exception as e:
            logger.exception("post-migration startup backup failed")
            _MAINT_STATUS["backup"] = {
                "state": "error", "at": _utcnow_iso(), "error": str(e)
            }


app = FastAPI(title="Insurance AI Local Engine", lifespan=_lifespan)
app.state.api_secret = API_SECRET
app.state.api_secret_header = API_SECRET_HEADER


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@app.post("/customers/{customer_id}/coverage-analysis/recalculate")
async def recalculate_coverage_analysis(
    customer_id: str,
    body: dict = Body(default_factory=dict),
    conn=Depends(get_db),
):
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = body or {}
    mode = body.get("mode") or "full"
    result = coverage.analyze(
        conn,
        customer_id,
        audience=body.get("audience") or "internal",
        persist=bool(body.get("persist", True)),
        force_recalculate=bool(body.get("force", True)),
        profile=body.get("profile") or {},
        mode=mode,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")

    run_id = result.get("run_id") or f"run-{secrets.token_hex(16)}"
    items = result.get("coverage_items") or result.get("categories") or []
    return {
        "run_id": run_id,
        "customer_id": customer_id,
        "status": "completed",
        "mode": mode,
        "started_at": started_at,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items_count": len(items),
        "links": {
            "result": f"/customers/{customer_id}/coverage-analysis?run_id={run_id}"
        },
    }

app.include_router(parser_router)
app.include_router(rag_router)
app.include_router(customer_router)
app.include_router(whisper_router)
app.include_router(auth_router)


@app.middleware("http")
async def require_api_secret(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/health", "/api-secret"}:
        return await call_next(request)
    if os.getenv("DEV_SKIP_AUTH") == "1":
        return await call_next(request)
    supplied = request.headers.get(API_SECRET_HEADER, "")
    if not secrets.compare_digest(supplied, API_SECRET):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


_MAX_REQUEST_BYTES = MAX_AUDIO  # 가장 큰 파일 종류(오디오) 기준. 세부 종류별 제한은 라우터가 담당.


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > _MAX_REQUEST_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"요청 본문이 너무 큽니다. 최대 {_MAX_REQUEST_BYTES // (1024 * 1024)}MB."},
        )
    return await call_next(request)


def _warm_ollama() -> None:
    """엔진 시작 시 필요한 모델을 미리 메모리에 올려둔다 (첫 요청 지연 제거)."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    keep = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
    intake_model = os.environ.get("INTAKE_LLM_MODEL", "qwen2.5:7b")
    rag_model = os.environ.get("RAG_LLM_MODEL", "qwen2.5:7b")
    embed_model = os.environ.get("RAG_EMBED_MODEL", "bge-m3")
    # options.num_ctx 는 실제 호출부(intake/summarizer 는 4096)와 같아야 재로딩이 없다.
    jobs = [
        ("/api/generate", {"model": intake_model, "prompt": "ping", "stream": False, "keep_alive": keep, "options": {"num_predict": 1, "num_ctx": 4096}}),
        ("/api/generate", {"model": rag_model, "prompt": "ping", "stream": False, "keep_alive": keep, "options": {"num_predict": 1}}),
        ("/api/embeddings", {"model": embed_model, "prompt": "ping", "keep_alive": keep}),
    ]
    for path, body in jobs:
        try:
            req = urllib.request.Request(
                base + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=180).read()
        except Exception:
            pass  # Ollama 꺼져 있으면 조용히 넘어감


def _warm_whisper() -> None:
    if os.environ.get("WHISPER_PREWARM", "1") == "0":
        return
    try:
        from whisper.transcriber import prewarm
        prewarm(blocking=True)
    except Exception:
        pass

# Tauri webview와 Vite 개발 서버에서 오는 요청만 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


@app.get("/api-secret")
def api_secret(request: Request):
    origin = request.headers.get("origin")
    if origin not in (None, *ALLOWED_ORIGINS):
        return JSONResponse(status_code=403, content={"detail": "Forbidden origin"})
    return {"secret": API_SECRET}


@app.get("/health")
def health():
    """Desktop 홈 화면에서 폴링할 엔드포인트."""
    engine_status = {"local_engine": "ok"}

    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in data.get("models", [])]
        engine_status["ollama"] = "connected"
        engine_status["models_available"] = models
    except Exception as e:
        engine_status["ollama"] = "disconnected"
        engine_status["error"] = str(e)

    # 고객 DB 암호화 상태 (설정·진단 화면에서 표시)
    try:
        from database.crypto import get_cipher
        from database.db import connect, init_schema
        from database.repo import count_plaintext_values

        cipher = get_cipher()
        conn = connect()
        try:
            init_schema(conn)
            plaintext_count = count_plaintext_values(conn)
        finally:
            conn.close()
        engine_status["customer_db"] = {
            "encryption": "AES-256-GCM (field-level)",
            "key_source": cipher.key_source,  # keyring | keyfile | env
            "plaintext_count": plaintext_count,
        }
        # 평문 잔존 또는 키체인 대신 키파일 폴백 — 둘 다 진단 화면에서 주의 표시.
        if plaintext_count > 0 or cipher.key_source == "keyfile":
            engine_status["customer_db"]["warning"] = True
    except Exception as e:
        engine_status["customer_db"] = {"encryption": "unavailable", "error": str(e)}

    try:
        from database import backup as _bk
        from database.db import resolve_db_path

        try:
            stale_days = int(os.environ.get("ENGINE_BACKUP_STALE_DAYS", "2"))
        except (TypeError, ValueError):
            stale_days = 2
        try:
            items = _bk.list_backups()
        except Exception:
            items = []
        snaps = [b for b in items if b.get("kind") == "snapshot"]
        last_at = snaps[0]["created_at"] if snaps else None
        age_days = None
        if last_at:
            try:
                age_days = (datetime.now() - datetime.fromisoformat(last_at)).days
            except (TypeError, ValueError):
                age_days = None
        if age_days is not None and age_days < 0:
            age_days = 0
        disabled = _MAINT_STATUS["backup"].get("disabled") is True
        warn = (not disabled) and (
            _MAINT_STATUS["backup"].get("state") == "error"
            or (last_at is None and resolve_db_path().exists())
            or (age_days is not None and age_days > stale_days)
        )
        m = {
            "last_backup_at": last_at,
            "last_backup_age_days": age_days,
            "backup_startup": _MAINT_STATUS["backup"],
            "usage_prune": _MAINT_STATUS["usage_prune"],
            "audit_prune": _MAINT_STATUS["audit_prune"],
        }
        if warn:
            m["warning"] = True
        engine_status["maintenance"] = m
    except Exception as e:
        engine_status["maintenance"] = {"error": str(e)}

    return engine_status


def _port_taken(host: str, port: int) -> bool:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _healthy(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn

    # 반드시 127.0.0.1만 — 외부에서 접근 불가
    host = "127.0.0.1"
    port = int(os.environ.get("ENGINE_PORT", "8420"))

    # 이미 정상 인스턴스가 점유 중이면 재사용(개발 스크립트 중복 기동·재시작 충돌 방지).
    if _port_taken(host, port):
        if _healthy(host, port):
            print(f"local-engine: :{port} already serving — reusing", file=sys.stderr)
            sys.exit(0)
        print(f"local-engine: :{port} taken by an unhealthy process — exiting", file=sys.stderr)
        sys.exit(1)
    # APP_LOCK_FILE 감시는 _lifespan 의 asyncio 태스크에서 (onefile 스레드 이슈 회피)

    uvicorn.run(app, host=host, port=port)
