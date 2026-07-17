import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from notifications import EmailNotifier, NotificationRouter


class _FakeChannel:
    def __init__(self, name="email", recipients=None):
        self.channel_name = name
        self._recipients = ["destino"] if recipients is None else recipients
        self.calls = []

    @property
    def recipients(self):
        return list(self._recipients)

    def notify_recipient_change(
        self, recipient, extension, status, timestamp, is_test=False, context=None
    ):
        self.calls.append((recipient, extension, status, timestamp, is_test, context))


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

    def test_email_channel_accepts_responsible_not_listed_as_global_recipient(self):
        channel = _FakeChannel("email", [])
        router = NotificationRouter([channel])

        router.notify_recipient_change(
            "email:ana@example.com",
            "1001",
            "offline",
            "14/07/2026 10:00:00",
            context={"nome": "Ana"},
        )

        self.assertTrue(router.can_deliver_recipient("email:ana@example.com"))
        self.assertEqual(channel.calls[0][0], "ana@example.com")
        self.assertEqual(channel.calls[0][-1]["nome"], "Ana")


class EmailNotifierTests(unittest.TestCase):
    def test_utf8_subject_round_trip_has_no_replacement_characters_or_test_prefix(self):
        notifier = EmailNotifier(
            host="smtp.example.com",
            port=587,
            sender="monitor@example.com",
        )

        message = notifier._build_message(
            "ana@example.com",
            "1001",
            "offline",
            "16/07/2026 15:00:00",
            True,
            context={"nome": "Ana", "setor": "Administração"},
        )
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())

        self.assertEqual(
            str(parsed["Subject"]),
            "Joinville Implementos - Teste de notificação do PulsoPBX",
        )
        self.assertNotIn("[TESTE]", str(parsed["Subject"]))
        self.assertNotIn("[PulsoPBX]", str(parsed["Subject"]))
        self.assertNotIn("?", str(parsed["Subject"]))

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
            "ti@example.com",
            "1001",
            "offline",
            "14/07/2026 10:00:00",
            context={"nome": "Ana", "setor": "Financeiro"},
        )

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("monitor@example.com", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "ti@example.com")
        self.assertEqual(
            message["Subject"],
            "Joinville Implementos - Ramal 1001 desconectado",
        )
        self.assertEqual(message["Content-Language"], "pt-BR")
        self.assertIsNotNone(message["Date"])
        self.assertTrue(str(message["Message-ID"]).endswith("@example.com>"))
        self.assertEqual(message["Auto-Submitted"], "auto-generated")
        self.assertEqual(message["X-Auto-Response-Suppress"], "All")
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("MicroSIP", plain)
        self.assertIn("Olá, Ana.", plain)
        self.assertIn("Financeiro", html)
        self.assertIn("Eduardo Porangaba Leite Ribeiro da Silva", html)
        self.assertIn("cid:joinville-logo", html)
        self.assertNotIn("linear-gradient", html)
        self.assertNotIn("box-shadow", html)
        self.assertNotIn("<ol", html)
        self.assertNotIn("Mensagem automática enviada pelo PulsoPBX", html)
        self.assertNotIn("Um único aviso é emitido por incidente", html)

        images = [part for part in message.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["Content-ID"], "<joinville-logo>")
        self.assertEqual(images[0].get_content_disposition(), "inline")

        serialized = message.as_bytes()
        self.assertIn(b"\r\n", serialized)
        self.assertNotIn(b"\n", serialized.replace(b"\r\n", b""))

    def test_missing_logo_uses_text_fallback_without_broken_cid(self):
        with TemporaryDirectory() as directory:
            notifier = EmailNotifier(
                host="smtp.example.com",
                port=587,
                sender="monitor@example.com",
                logo_path=Path(directory) / "missing.png",
            )
            message = notifier._build_message(
                "ti@example.com",
                "1001",
                "offline",
                "14/07/2026 10:00:00",
            )

        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("cid:joinville-logo", html)
        self.assertIn("JOINVILLE", html)
        self.assertFalse(any(part.get_content_maintype() == "image" for part in message.walk()))

    def test_recovery_email_keeps_signature_and_duration(self):
        notifier = EmailNotifier(
            host="smtp.example.com",
            port=587,
            sender="monitor@example.com",
        )

        message = notifier._build_message(
            "ti@example.com",
            "1001",
            "online",
            "14/07/2026 10:05:00",
            context={
                "nome": "Ana",
                "setor": "Financeiro",
                "duration_seconds": 300,
            },
        )

        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Ramal 1001 reconectado", message["Subject"])
        self.assertIn("aproximadamente 5 minutos úteis", plain)
        self.assertIn("CONEXÃO RESTABELECIDA", html)
        self.assertIn("Atenciosamente", html)

    def test_dynamic_fields_are_html_escaped(self):
        notifier = EmailNotifier(
            host="smtp.example.com",
            port=587,
            sender="monitor@example.com",
        )

        message = notifier._build_message(
            "ti@example.com",
            "<1001>",
            "offline",
            "14/07/2026 10:00:00",
            context={"nome": "<script>alert(1)</script>", "setor": "TI & Suporte"},
        )

        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("TI &amp; Suporte", html)
