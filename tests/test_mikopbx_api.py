import unittest
from unittest.mock import Mock, patch

import requests

import mikopbx_api


class MikoPbxCacheTests(unittest.TestCase):
    def setUp(self):
        with mikopbx_api._cache_lock:
            mikopbx_api._cache = {}
            mikopbx_api._profiles_cache = {}
            mikopbx_api._cache_ready = False

    def test_successful_refresh_atomically_replaces_cache(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "result": True,
            "data": {"data": [{"number": "1001", "user_username": "Ana", "user_email": "ANA@EXAMPLE.COM", "disabled": False}]},
        }
        with patch.object(mikopbx_api.requests, "get", return_value=response):
            names = mikopbx_api.refresh("https://pbx/api/v3", "token", False)

        self.assertEqual(names, {"1001": "Ana"})
        self.assertEqual(mikopbx_api.get_cached_names(), {"1001": "Ana"})
        self.assertEqual(
            mikopbx_api.get_cached_profiles()["1001"]["email"],
            "ana@example.com",
        )
        self.assertTrue(mikopbx_api.is_cache_ready())

    def test_failed_refresh_preserves_last_valid_cache(self):
        with mikopbx_api._cache_lock:
            mikopbx_api._cache = {"1001": "Ana"}
            mikopbx_api._profiles_cache = {
                "1001": {"nome": "Ana", "email": "ana@example.com"}
            }
            mikopbx_api._cache_ready = True
        with patch.object(
            mikopbx_api.requests, "get", side_effect=requests.ConnectionError("offline")
        ), self.assertLogs("mikopbx_api", level="ERROR"):
            names = mikopbx_api.refresh("https://pbx/api/v3", "token", False)

        self.assertIsNone(names)
        self.assertEqual(mikopbx_api.get_cached_names(), {"1001": "Ana"})
        self.assertEqual(
            mikopbx_api.get_cached_profiles()["1001"]["email"],
            "ana@example.com",
        )
