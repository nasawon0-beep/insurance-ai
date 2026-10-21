"""Run the isolated TLS LAN sync adapter.

Example (certificate/key are operator-managed; no key is generated here):
  python -m lan_sync.server --db /path/to/customers.sqlite3 --cert cert.pem --key key.pem
"""
from __future__ import annotations

import argparse
import hashlib
import ssl
from pathlib import Path

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="existing production customer SQLite database")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--host", required=True, help="explicit private LAN interface address")
    parser.add_argument("--port", type=int, default=18443)
    args = parser.parse_args()
    for path in (args.cert, args.key):
        if not Path(path).is_file():
            parser.error(f"TLS file does not exist: {path}")
    pem = Path(args.cert).read_text(encoding="utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    fingerprint = hashlib.sha256(der).hexdigest()
    print(f"certificate_sha256={fingerprint}")
    print(f"listen=https://{args.host}:{args.port}")
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, ssl_certfile=args.cert, ssl_keyfile=args.key)


if __name__ == "__main__":
    main()
