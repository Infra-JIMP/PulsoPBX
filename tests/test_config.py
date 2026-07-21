import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid_without_optional_integrations(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_secret = Path(directory) / "nao-existe.txt"
            with patch.dict(
                os.environ,
                {"RESPONSIBLES_ADMIN_PASSWORD_FILE": str(missing_secret)},
                clear=True,
            ):
                config = load_config()
        self.assertEqual(config.dashboard_port, 8080)
        self.assertFalse(config.ami_enabled)
        self.assertFalse(config.email_enabled)
        self.assertFalse(config.notifications_enabled)
        self.assertFalse(config.responsibles_admin_enabled)
        self.assertFalse(config.cloud_sync_enabled)
        self.assertEqual(config.email_subject_brand, "Joinville Implementos")

    def test_cloud_sync_requires_url_and_token_together(self):
        with patch.dict(
            os.environ,
            {
                "CLOUD_SYNC_URL": "https://pulsopbx.vercel.app",
                "CLOUD_SYNC_TOKEN_FILE": "data/token-inexistente-no-teste.txt",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigError, "CLOUD_SYNC_TOKEN"):
                load_config()

    def test_cloud_sync_requires_https(self):
        with patch.dict(
            os.environ,
            {"CLOUD_SYNC_URL": "http://example.test", "CLOUD_SYNC_TOKEN": "segredo"},
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigError, "HTTPS"):
                load_config()

    def test_zero_reconcile_interval_is_rejected(self):
        with patch.dict(os.environ, {"RECONCILE_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "RECONCILE_SECONDS"):
                load_config()

    def test_invalid_boolean_is_rejected(self):
        with patch.dict(os.environ, {"DEMO_MODE": "talvez"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "DEMO_MODE"):
                load_config()

    def test_invalid_port_is_rejected(self):
        with patch.dict(os.environ, {"DASHBOARD_PORT": "70000"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "DASHBOARD_PORT"):
                load_config()

    def test_recipients_are_trimmed_and_deduplicated(self):
        values = {
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_FROM": "monitor@example.com",
            "EMAIL_RECIPIENTS": "ti@example.com, suporte@example.com,ti@example.com",
        }
        with patch.dict(os.environ, values, clear=True):
            config = load_config()
        self.assertEqual(config.email_recipients, ["ti@example.com", "suporte@example.com"])

    def test_email_is_enabled_when_complete(self):
        values = {
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_FROM": "monitor@example.com",
            "EMAIL_RECIPIENTS": "ti@example.com",
        }
        with patch.dict(os.environ, values, clear=True):
            config = load_config()
        self.assertTrue(config.email_enabled)
        self.assertEqual(config.enabled_notification_channels, ["email"])
        self.assertEqual(config.notification_target_count, 1)

    def test_email_subject_brand_is_configurable(self):
        with patch.dict(
            os.environ,
            {"EMAIL_SUBJECT_BRAND": "JIMP"},
            clear=True,
        ):
            config = load_config()

        self.assertEqual(config.email_subject_brand, "JIMP")

    def test_empty_email_subject_brand_is_rejected(self):
        with patch.dict(
            os.environ,
            {"EMAIL_SUBJECT_BRAND": " "},
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigError, "EMAIL_SUBJECT_BRAND"):
                load_config()

    def test_email_channel_can_use_only_responsible_addresses_from_mikopbx(self):
        values = {
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_FROM": "monitor@example.com",
        }
        with patch.dict(os.environ, values, clear=True):
            config = load_config()
        self.assertTrue(config.email_enabled)
        self.assertEqual(config.email_recipients, [])
        self.assertEqual(config.responsible_alert_delay_seconds, 120)
        self.assertEqual(config.mass_outage_threshold, 5)

    def test_partial_email_configuration_is_rejected(self):
        with patch.dict(os.environ, {"EMAIL_SMTP_HOST": "smtp.example.com"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "EMAIL_FROM"):
                load_config()

    def test_removed_integrations_are_not_part_of_config(self):
        with patch.dict(
            os.environ,
            {"TEAMS_WEBHOOK_URL": "https://example.com/hook", "WHATSAPP_TOKEN": "token"},
            clear=True,
        ):
            config = load_config()
        self.assertFalse(hasattr(config, "teams_enabled"))
        self.assertFalse(hasattr(config, "whatsapp_enabled"))

    def test_dashboard_credentials_must_be_complete(self):
        with patch.dict(os.environ, {"DASHBOARD_USERNAME": "pulsopbx"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "DASHBOARD_PASSWORD"):
                load_config()

    def test_responsibles_admin_password_can_come_from_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "admin.txt"
            secret_file.write_text("uma-senha-administrativa-segura", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"RESPONSIBLES_ADMIN_PASSWORD_FILE": str(secret_file)},
                clear=True,
            ):
                config = load_config()
        self.assertTrue(config.responsibles_admin_enabled)
        self.assertEqual(config.responsibles_admin_password, "uma-senha-administrativa-segura")

    def test_short_responsibles_admin_password_is_rejected(self):
        with patch.dict(
            os.environ,
            {"RESPONSIBLES_ADMIN_PASSWORD": "curta"},
            clear=True,
        ):
            with self.assertRaisesRegex(ConfigError, "pelo menos 8"):
                load_config()
