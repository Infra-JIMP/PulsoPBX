import os
import unittest
from unittest.mock import patch

from config import load_config


class CloudSyncConfigTests(unittest.TestCase):
    def test_cloud_sync_can_be_enabled_safely(self):
        values = {
            "CLOUD_SYNC_URL": "https://pulsopbx.vercel.app/",
            "CLOUD_SYNC_TOKEN": "token-seguro",
            "CLOUD_SYNC_INTERVAL_SECONDS": "20",
        }
        with patch.dict(os.environ, values, clear=True):
            config = load_config()

        self.assertTrue(config.cloud_sync_enabled)
        self.assertEqual(config.cloud_sync_url, "https://pulsopbx.vercel.app")
        self.assertEqual(config.cloud_sync_interval_seconds, 20)
        self.assertTrue(config.cloud_sync_verify_tls)
