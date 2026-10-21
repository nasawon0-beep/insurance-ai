from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from lan_sync import server


class LanSyncServerSecurityTest(unittest.TestCase):
    def test_server_does_not_trust_forwarded_client_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert = Path(tmp) / "cert.pem"
            key = Path(tmp) / "key.pem"
            cert.write_text("synthetic-cert", encoding="utf-8")
            key.write_text("synthetic-key", encoding="utf-8")
            argv = [
                "lan-sync-server",
                "--db", str(Path(tmp) / "synthetic.sqlite3"),
                "--cert", str(cert),
                "--key", str(key),
                "--host", "127.0.0.1",
            ]
            with patch.object(sys, "argv", argv), patch.object(
                server.ssl, "PEM_cert_to_DER_cert", return_value=b"synthetic-der"
            ), patch.object(server, "create_app", return_value="synthetic-app"), patch.object(
                server.uvicorn, "run"
            ) as run:
                server.main()

        self.assertFalse(run.call_args.kwargs["proxy_headers"])
        self.assertEqual("", run.call_args.kwargs["forwarded_allow_ips"])


if __name__ == "__main__":
    unittest.main()
