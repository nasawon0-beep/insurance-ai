from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-engine"))
from lan_sync.store import AuthError, PairingRateLimitError, SyncStore


class FakeClock:
    def __init__(self, now: int = 1_000_000):
        self.now = now

    def __call__(self) -> float:
        return float(self.now)

    def advance(self, seconds: int) -> None:
        self.now += seconds


class PairingRateLimitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pairing-rate.sqlite3"
        self.clock = FakeClock()
        self.store = SyncStore(self.db, clock=self.clock)
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def fail_pairing(self, source: str = "192.0.2.10") -> None:
        with self.assertRaises(AuthError):
            self.store.pair_device("000000", "synthetic-phone", source)

    def test_five_failures_block_source_with_retry_after(self):
        for _ in range(5):
            self.fail_pairing()

        with self.assertRaises(PairingRateLimitError) as raised:
            self.store.pair_device("000000", "synthetic-phone", "192.0.2.10")

        self.assertEqual(30, raised.exception.retry_after)

    def test_source_recovers_after_cooldown_without_sleeping(self):
        for _ in range(5):
            self.fail_pairing()
        self.clock.advance(30)
        code = self.store.create_pairing_code("synthetic-pc", ttl_seconds=60)

        paired = self.store.pair_device(code.code, "synthetic-phone", "192.0.2.10")

        self.assertTrue(paired.token)

    def test_limit_survives_store_restart(self):
        for _ in range(5):
            self.fail_pairing()
        self.store.close()
        self.store = SyncStore(self.db, clock=self.clock)
        self.store.initialize()

        with self.assertRaises(PairingRateLimitError) as raised:
            self.store.pair_device("000000", "synthetic-phone", "192.0.2.10")

        self.assertEqual(30, raised.exception.retry_after)

    def test_global_limit_blocks_distributed_source_flood(self):
        for number in range(30):
            self.fail_pairing(f"198.51.100.{number}")

        with self.assertRaises(PairingRateLimitError) as raised:
            self.store.pair_device("000000", "synthetic-phone", "203.0.113.1")

        self.assertEqual(60, raised.exception.retry_after)
        self.clock.advance(60)
        code = self.store.create_pairing_code("synthetic-pc", ttl_seconds=60)
        paired = self.store.pair_device(code.code, "synthetic-phone", "203.0.113.1")
        self.assertTrue(paired.token)

    def test_parallel_attempts_cannot_bypass_source_limit(self):
        barrier = threading.Barrier(20)

        def attempt(_: int) -> str:
            barrier.wait()
            try:
                self.store.pair_device("000000", "synthetic-phone", "192.0.2.44")
            except PairingRateLimitError:
                return "limited"
            except AuthError:
                return "invalid"
            return "unexpected"

        with ThreadPoolExecutor(max_workers=20) as executor:
            outcomes = list(executor.map(attempt, range(20)))

        self.assertEqual(5, outcomes.count("invalid"))
        self.assertEqual(15, outcomes.count("limited"))
        self.assertNotIn("unexpected", outcomes)

    def test_expired_and_reused_codes_are_rejected_but_valid_code_pairs(self):
        expired = self.store.create_pairing_code("synthetic-pc", ttl_seconds=5)
        self.clock.advance(6)
        with self.assertRaises(AuthError):
            self.store.pair_device(expired.code, "phone-expired", "192.0.2.50")

        valid = self.store.create_pairing_code("synthetic-pc", ttl_seconds=60)
        paired = self.store.pair_device(valid.code, "phone-valid", "192.0.2.51")
        self.assertTrue(paired.token)
        with self.assertRaises(AuthError):
            self.store.pair_device(valid.code, "phone-reuse", "192.0.2.52")

    def test_source_bucket_storage_remains_bounded_under_rotating_inputs(self):
        for number in range(300):
            source = f"rotating-source-{number}"
            try:
                self.store.pair_device("000000", "synthetic-phone", source)
            except PairingRateLimitError:
                self.clock.advance(60)
                with self.assertRaises(AuthError):
                    self.store.pair_device("000000", "synthetic-phone", source)
            except AuthError:
                pass

        source_rows = self.store.conn.execute(
            "SELECT COUNT(*) FROM sync_pairing_rate_limits WHERE bucket_type='source'"
        ).fetchone()[0]
        self.assertLessEqual(source_rows, 257)


if __name__ == "__main__":
    unittest.main()
