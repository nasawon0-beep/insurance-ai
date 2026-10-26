from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from lan_sync import admin, server


class LanSyncServerSecurityTest(unittest.TestCase):
    def test_admin_certificate_json_is_ascii_safe_and_round_trips_windows_unicode_paths(self):
        result = {
            "cert_path": r"\\?\C:\Users\한글 사용자\mobile-sync-cert.pem",
            "key_path": r"\\?\C:\Users\한글 사용자\mobile-sync-key.pem",
            "fingerprint": "00" * 32,
            "expires_at": 1,
            "hosts": ["192.168.1.20"],
            "created": True,
        }
        output = io.StringIO()
        argv = [
            "lan-sync-admin",
            "ensure-certificate",
            "--state-dir",
            r"\\?\C:\Users\한글 사용자\mobile-sync",
            "--host",
            "192.168.1.20",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            admin, "ensure_certificate", return_value=result
        ), redirect_stdout(output):
            admin.main()

        line = output.getvalue().strip()
        self.assertTrue(line.isascii())
        self.assertEqual(result, json.loads(line))

    def test_server_does_not_trust_forwarded_client_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "합성-프로필"
            fixture.mkdir()
            cert = fixture / "cert.pem"
            key = fixture / "key.pem"
            shutdown_file = fixture / "stop.request"
            cert.write_text("synthetic-cert", encoding="utf-8")
            key.write_text("synthetic-key", encoding="utf-8")
            argv = [
                "lan-sync-server",
                "--db", str(fixture / "synthetic.sqlite3"),
                "--cert", str(cert),
                "--key", str(key),
                "--host", "192.168.1.20",
                "--port", "18443",
                "--instance-id", "0123456789abcdef0123456789abcdef",
                "--shutdown-file", str(shutdown_file),
            ]
            with patch.object(sys, "argv", argv), patch.object(
                server.ssl, "PEM_cert_to_DER_cert", return_value=b"synthetic-der"
            ), patch.object(server, "create_app", return_value="synthetic-app"), patch.object(
                server.uvicorn, "Config"
            ) as config, patch.object(
                server, "_serve", new=lambda *args: "synthetic-serve"
            ), patch.object(server.asyncio, "run") as run:
                server.main()

        self.assertFalse(config.call_args.kwargs["proxy_headers"])
        self.assertEqual("", config.call_args.kwargs["forwarded_allow_ips"])
        self.assertEqual("192.168.1.20", config.call_args.kwargs["host"])
        self.assertEqual(18443, config.call_args.kwargs["port"])
        run.assert_called_once_with("synthetic-serve")


if __name__ == "__main__":
    unittest.main()
