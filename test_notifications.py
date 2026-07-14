import unittest
from unittest.mock import patch

from notifications import EmailNotifier, NotificationRouter


class _FakeChannel:
    def __init__(self, name="email", recipients=None):
        self.channel_name = name
        self._recipients = recipients or ["destino"]
        self.calls = []

    @property
    def recipients(self):
        return list(self._recipients)

    def notify_recipient_change(self, recipient, extension, status, timestamp, is_test=False):
        self.calls.append((recipient, extension, status, timestamp, is_test))


class NotificationRouterTests(unittest.TestCase):
    def test_router_uses_channel_qualified_target(self):
        channel = _FakeChannel("email", ["ti@example.com"])
        router = NotificationRouter([channel])

        router.notify_recipient_change(
            "email:ti@example.com", "1001", "offline", "14/07/2026 10:00:00"
        )

        self.assertEqual(router.recipients, ["email:ti@example.com"])
        self.assertEqual(channel.calls[0][:3], ("ti@example.com", "1001", "offline"))

    def test_removed_recipient_is_not_deliverable(self):
        channel = _FakeChannel("email", ["ti@example.com"])
        router = NotificationRouter([channel])

        self.assertFalse(router.can_deliver_recipient("whatsapp:5547999999999"))


class EmailNotifierTests(unittest.TestCase):
    @patch("notifications.smtplib.SMTP")
    def test_email_uses_starttls_and_sends_to_one_recipient(self, smtp_class):
        smtp = smtp_class.return_value.__enter__.return_value
        notifier = EmailNotifier(
            host="smtp.example.com",
            port=587,
            sender="monitor@example.com",
            recipients=["ti@example.com"],
            username="monitor@example.com",
            password="secret",
        )

        notifier.notify_recipient_change(
            "ti@example.com", "1001", "offline", "14/07/2026 10:00:00"
        )

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("monitor@example.com", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "ti@example.com")
        self.assertIn("Ramal 1001 indisponivel", message["Subject"])
