"""Run the isolated TLS LAN sync adapter.

Example (certificate/key are operator-managed; no key is generated here):
  python -m lan_sync.server --db /path/to/customers.sqlite3 --cert cert.pem --key key.pem
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import ssl
from pathlib import Path

import uvicorn

from .app import create_app


def _parent_lock_is_free(lock_path: str) -> bool:
    try:
        descriptor = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


async def _serve(config: uvicorn.Config, shutdown_file: Path | None, app_lock: str) -> None:
    server = uvicorn.Server(config)

    async def watch_lifecycle() -> None:
        while not server.should_exit:
            if shutdown_file is not None and shutdown_file.exists():
                server.should_exit = True
                return
            if app_lock and _parent_lock_is_free(app_lock):
                server.should_exit = True
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch_lifecycle())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="existing production customer SQLite database")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--host", required=True, help="explicit private LAN interface address")
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--instance-id", required=True, help="stable non-secret PC discovery identity")
    parser.add_argument("--shutdown-file")
    args = parser.parse_args()
    if len(args.instance_id) != 32 or any(character not in "0123456789abcdef" for character in args.instance_id):
        parser.error("instance-id must be 32 lowercase hexadecimal characters")
    for path in (args.cert, args.key):
        if not Path(path).is_file():
            parser.error(f"TLS file does not exist: {path}")
    pem = Path(args.cert).read_text(encoding="utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    fingerprint = hashlib.sha256(der).hexdigest()
    print(f"certificate_sha256={fingerprint}")
    print(f"listen=https://{args.host}:{args.port}")
    shutdown_file = Path(args.shutdown_file) if args.shutdown_file else None
    if shutdown_file is not None:
        shutdown_file.unlink(missing_ok=True)
    config = uvicorn.Config(
        create_app(args.db, pc_instance_id=args.instance_id),
        host=args.host,
        port=args.port,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
        proxy_headers=False,
        forwarded_allow_ips="",
    )
    asyncio.run(_serve(config, shutdown_file, os.environ.get("APP_LOCK_FILE", "")))


if __name__ == "__main__":
    main()
