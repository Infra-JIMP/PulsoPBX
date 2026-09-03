import unittest
from unittest.mock import patch

from profiles import load_profiles, notification_target


class ProfileTests(unittest.TestCase):
    @patch("profiles.load_names", return_value={})
    @patch(
        "profiles.mikopbx_api.get_cached_profiles",
        return_value={
            "1001": {"nome": "Ana", "email": "ana@example.com"},
            "1002": {"nome": "Bia", "email": "endereco-invalido"},
        },
    )
    def test_only_valid_email_becomes_notification_target(self, _profiles, _names):
        profiles = load_profiles()

        self.assertEqual(profiles["1001"]["email"], "ana@example.com")
        self.assertEqual(profiles["1002"]["email"], "")
        self.assertEqual(notification_target("1001")[0], "email:ana@example.com")

    @patch(
        "profiles.load_names",
        return_value={"1001": {"nome": "", "setor": "", "email": "ana@example.com", "notificar": False}},
    )
    @patch(
        "profiles.mikopbx_api.get_cached_profiles",
        return_value={"1001": {"nome": "Ana", "email": "ana@example.com"}},
    )
    def test_local_override_can_disable_notifications(self, _profiles, _names):
        target, profile = notification_target("1001")

        self.assertIsNone(target)
        self.assertFalse(profile["notificar"])


if __name__ == "__main__":
    unittest.main()
