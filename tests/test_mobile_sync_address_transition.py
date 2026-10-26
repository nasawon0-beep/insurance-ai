from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from cryptography import x509

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mobile_sync_address_transition.py"
spec = importlib.util.spec_from_file_location("mobile_sync_address_transition", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MobileSyncAddressTransitionTest(unittest.TestCase):
    def test_stages_dynamic_address_certificate_and_narrow_firewall_without_touching_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir()
            for name in ("synthetic.sqlite3", "active-cert.pem", "active-key.pem"):
                (root / name).write_text(f"original-{name}", encoding="utf-8")
            state = {
                "root": str(root),
                "db": str(root / "synthetic.sqlite3"),
                "cert": str(root / "active-cert.pem"),
                "key": str(root / "active-key.pem"),
                "host": "192.0.2.10",
                "port": 55902,
            }
            state_path = Path(temporary) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            state_before = state_path.read_bytes()
            active_before = {name: (root / name).read_bytes() for name in ("active-cert.pem", "active-key.pem", "synthetic.sqlite3")}

            plan = module.stage_transition(
                state_path,
                Path(temporary) / "stage",
                {"interface_index": 10, "host": "198.51.100.10", "gateway": "198.51.100.20"},
            )

            certificate = x509.load_pem_x509_certificate(Path(plan["staged_cert"]).read_bytes())
            sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual({"198.51.100.10", "127.0.0.1"}, {str(item) for item in sans.get_values_for_type(x509.IPAddress)})
            self.assertEqual("198.51.100.10", plan["new_host"])
            self.assertEqual("198.51.100.20", plan["remote_ip"])
            self.assertIn("profile=private", plan["firewall_add"])
            self.assertIn("localip=198.51.100.10", plan["firewall_add"])
            self.assertIn("remoteip=198.51.100.20", plan["firewall_add"])
            self.assertIn("localport=55902", plan["firewall_add"])
            self.assertTrue(plan["requires_server_stopped_before_apply"])
            self.assertFalse(plan["live_state_modified"])
            self.assertEqual(state_before, state_path.read_bytes())
            self.assertEqual(active_before, {name: (root / name).read_bytes() for name in active_before})

    def test_refuses_to_overwrite_existing_stage_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir()
            for name in ("synthetic.sqlite3", "active-cert.pem", "active-key.pem"):
                (root / name).write_text("fixture", encoding="utf-8")
            state_path = Path(temporary) / "state.json"
            state_path.write_text(json.dumps({
                "root": str(root), "db": str(root / "synthetic.sqlite3"),
                "cert": str(root / "active-cert.pem"), "key": str(root / "active-key.pem"),
                "host": "192.0.2.10", "port": 55902,
            }), encoding="utf-8")
            stage = Path(temporary) / "stage"
            stage.mkdir()
            (stage / "tls-cert.pem").write_text("do-not-overwrite", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "already contains"):
                module.stage_transition(
                    state_path,
                    stage,
                    {"interface_index": 10, "host": "198.51.100.10", "gateway": "198.51.100.20"},
                )


if __name__ == "__main__":
    unittest.main()
