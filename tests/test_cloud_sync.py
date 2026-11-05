import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cloud_sync import CloudSync
from config import ConfigError, load_config
from state import StateTracker

TOKEN = "t" * 40


class _Response:
    def __init__(self, status=200):
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Poster:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, url, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _Response(500 if self.fail else 200)


PROFILES = {
    "1001": {"nome": "Ana", "setor": "Compras", "email": "ana@empresa.com.br"},
    "1002": {"nome": "Bruno", "setor": "Financeiro", "email": "bruno@empresa.com.br"},
}


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.tracker = StateTracker(debounce_seconds=30)
        self.tracker.update("1001", True, now=1000)
        self.tracker.update("1002", True, now=1000)
        self.client = SimpleNamespace(connected=True, last_reconcile_at=1000)
        self.poster = _Poster()
        self.sync = CloudSync(
            self.tracker,
            self.client,
            "https://pulsopbx.vercel.app/",
            TOKEN,
            interval_seconds=30,
            heartbeat_seconds=600,
            profile_loader=lambda: PROFILES,
            poster=self.poster,
        )

    def test_first_run_sends_minimal_snapshot_without_emails(self):
        self.assertTrue(self.sync.sync_once(now=1010))
        call = self.poster.calls[0]
        self.assertEqual(call["url"], "https://pulsopbx.vercel.app/api/push")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {TOKEN}")
        payload = call["json"]
        self.assertEqual(payload["ami_status"], "connected")
        self.assertEqual(
            set(payload["extensions"][0]),
            {"extension", "nome", "setor", "online", "since", "pending_status", "pending_since"},
        )
        self.assertNotIn("@", str(payload))

    def test_nothing_is_sent_while_nothing_changes(self):
        self.sync.sync_once(now=1010)
        for now in range(1040, 1600, 30):
            self.client.last_reconcile_at = now  # reconciliacao da AMI a cada ciclo
            self.assertFalse(self.sync.sync_once(now=now))
        self.assertEqual(len(self.poster.calls), 1)

    def test_state_change_is_sent(self):
        self.sync.sync_once(now=1010)
        self.tracker.update("1002", False, now=1020)
        self.assertTrue(self.sync.sync_once(now=1040))  # entrou em confirmacao
        self.tracker.tick(now=1060)
        self.assertTrue(self.sync.sync_once(now=1070))  # queda confirmada
        self.assertFalse(self.sync.sync_once(now=1100))
        self.assertEqual(len(self.poster.calls), 3)

    def test_heartbeat_is_sent_after_quiet_period(self):
        self.sync.sync_once(now=1010)
        self.assertFalse(self.sync.sync_once(now=1500))
        self.assertTrue(self.sync.sync_once(now=1610))

    def test_failures_back_off_instead_of_retrying_every_cycle(self):
        self.poster.fail = True
        self.assertFalse(self.sync.sync_once(now=1010))
        self.assertFalse(self.sync.sync_once(now=1040))  # ainda dentro da espera
        self.assertEqual(len(self.poster.calls), 1)
        self.poster.fail = False
        self.assertTrue(self.sync.sync_once(now=1010 + 60))
        self.assertEqual(len(self.poster.calls), 2)

    def test_daily_budget_stays_far_below_the_free_redis_quota(self):
        # Pior caso: um ramal oscilando o dia inteiro, verificacao a cada 30s.
        now = 1010
        for step in range(2880):
            self.tracker.update("1001", step % 2 == 0, now=now)
            self.tracker.tick(now=now)
            self.sync.sync_once(now=now)
            now += 30
        self.assertLessEqual(len(self.poster.calls), 2880)
        self.assertLess(len(self.poster.calls) * 22, 500_000)


class CloudSyncConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
        self.assertFalse(config.cloud_sync_enabled)
        self.assertEqual(config.cloud_sync_seconds, 30)

    def test_requires_https_and_long_token(self):
        cases = [
            ({"CLOUD_SYNC_URL": "https://pulsopbx.vercel.app"}, "juntos"),
            ({"CLOUD_SYNC_URL": "http://pulsopbx.vercel.app", "CLOUD_SYNC_TOKEN": TOKEN}, "https"),
            ({"CLOUD_SYNC_URL": "https://pulsopbx.vercel.app", "CLOUD_SYNC_TOKEN": "curto"}, "32"),
            ({"CLOUD_SYNC_URL": "https://x.app", "CLOUD_SYNC_TOKEN": TOKEN, "CLOUD_SYNC_SECONDS": "5"}, "CLOUD_SYNC_SECONDS"),
        ]
        for values, message in cases:
            with self.subTest(values=values):
                with patch.dict(os.environ, values, clear=True):
                    with self.assertRaisesRegex(ConfigError, message):
                        load_config()

    def test_enabled_when_complete(self):
        values = {"CLOUD_SYNC_URL": "https://pulsopbx.vercel.app/", "CLOUD_SYNC_TOKEN": TOKEN}
        with patch.dict(os.environ, values, clear=True):
            config = load_config()
        self.assertTrue(config.cloud_sync_enabled)
        self.assertEqual(config.cloud_sync_url, "https://pulsopbx.vercel.app")


if __name__ == "__main__":
    unittest.main()
