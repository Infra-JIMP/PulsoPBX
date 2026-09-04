import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from alerts import AlertDispatcher
from directory import DirectoryStore
from email_templates import build_email_content
from web import create_app


class _RecordingNotifier:
    def __init__(self, recipients=None):
        self.recipients = list(recipients or [])
        self.sent = []

    def can_deliver_recipient(self, recipient):
        channel, separator, target = recipient.partition(":")
        return bool(separator and target and channel == "email")

    def notify_recipient_change(self, recipient, extension, status, timestamp, is_test=False, context=None):
        self.sent.append((recipient, extension, status, context))


class WelcomeEmailContentTests(unittest.TestCase):
    def _content(self, extension="1042", **context):
        payload = {"event_type": "welcome", "nome": "Ana", "setor": "Financeiro"}
        payload.update(context)
        return build_email_content(extension, "online", "03/09/2026 09:30:00", context=payload)

    def test_subject_and_body_present_the_new_extension(self):
        content = self._content(cargo="Analista Fiscal")

        self.assertIn("1042", content.subject)
        self.assertIn("Bem-vindo(a)", content.subject)
        self.assertIn("Analista Fiscal", content.plain_text)
        self.assertIn("Financeiro", content.plain_text)
        self.assertIn("Cadastro ativo", content.plain_text)
        self.assertIn("Boas-vindas: seu ramal está ativo", content.html_text)

    def test_steps_use_the_welcome_heading_instead_of_reconnection(self):
        content = self._content(steps=("Abra o MicroSIP.", "Transf. ligação: disque *3."))

        self.assertIn("Primeiros passos", content.plain_text)
        self.assertIn("Primeiros passos", content.html_text)
        self.assertNotIn("Como tentar reconectar", content.plain_text)
        self.assertIn("Transf. ligação: disque *3.", content.plain_text)

    def test_person_without_extension_still_receives_a_readable_message(self):
        content = self._content(extension="")

        self.assertNotIn("None", content.subject)
        self.assertIn("Não atribuído", content.plain_text)
        self.assertIn("ainda não atribuído", content.plain_text)

    def test_escapes_html_coming_from_the_registration_form(self):
        content = self._content(nome="<script>alerta</script>")

        self.assertNotIn("<script>", content.html_text)
        self.assertIn("&lt;script&gt;", content.html_text)


class WelcomeEmailDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_welcome_does_not_become_the_latest_extension_status(self):
        notifier = _RecordingNotifier()
        dispatcher = AlertDispatcher(notifier)

        dispatcher.enqueue_welcome("1042", ["email:ana@example.com"], {"nome": "Ana"})

        self.assertIsNone(dispatcher.get_extension_status("1042", "online"))
        event = dispatcher.recent_events()[0]
        self.assertEqual(event["extension"], "1042")
        self.assertEqual(event["total_recipients"], 1)

    async def test_offline_alert_after_welcome_is_not_deduplicated(self):
        notifier = _RecordingNotifier()
        dispatcher = AlertDispatcher(notifier)

        dispatcher.enqueue_welcome("1042", ["email:ana@example.com"])
        result = dispatcher.enqueue("1042", "offline", recipients=["email:ana@example.com"])

        self.assertFalse(result.get("deduplicated", False))


class WelcomeEmailEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = DirectoryStore(Path(self.temporary.name) / "pulsopbx.db")
        self.directory.initialize()
        self.notifier = _RecordingNotifier()
        self.alerts = AlertDispatcher(self.notifier)
        self.config = SimpleNamespace(
            dashboard_auth_enabled=False,
            demo_mode=False,
            responsibles_admin_enabled=True,
            responsibles_admin_password="senha-administrativa-segura",
            welcome_email_enabled=True,
            welcome_email_copy_recipients=["ti@example.com"],
        )
        app = create_app(
            None, None, self.config, alerts=self.alerts, directory=self.directory
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {
            "X-PulsoPBX-Admin": "senha-administrativa-segura",
            "X-PulsoPBX-Action": "manage-responsibles",
        }

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.close()
        self.temporary.cleanup()

    async def _create(self, **overrides):
        body = {
            "name": "Ana Souza",
            "role": "Analista Fiscal",
            "sector": "Fiscal",
            "extension": "1042",
            "email": "ana@example.com",
            "active": True,
            "notify": True,
        }
        body.update(overrides)
        response = await self.client.post(
            "/api/admin/directory", json=body, headers=self.headers
        )
        return response, await response.json()

    async def test_new_collaborator_receives_welcome_with_copy_to_it(self):
        response, payload = await self._create()

        self.assertEqual(response.status, 201)
        self.assertTrue(payload["welcome"]["sent"])
        self.assertEqual(payload["welcome"]["recipient_count"], 2)
        event = self.alerts.recent_events()[0]
        self.assertEqual(event["extension"], "1042")
        self.assertEqual(event["total_recipients"], 2)

    async def test_welcome_is_recorded_and_never_repeats(self):
        _, payload = await self._create()
        person_id = payload["person"]["id"]

        people = self.directory.list_people()
        stored = next(item for item in people if item["id"] == person_id)
        self.assertIsNotNone(stored["welcome_email_sent_at"])
        self.assertTrue(
            any(change["action"] == "welcome_email" for change in self.directory.recent_changes())
        )

        response = await self.client.put(
            f"/api/admin/directory/{person_id}",
            json={**payload["person"], "role": "Coordenadora"},
            headers=self.headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.alerts.recent_events()), 1)

    async def test_registration_without_email_is_saved_but_not_notified(self):
        response, payload = await self._create(email="", extension="1043")

        self.assertEqual(response.status, 201)
        self.assertFalse(payload["welcome"]["sent"])
        self.assertEqual(payload["welcome"]["reason"], "email_missing")
        self.assertEqual(self.alerts.recent_events(), [])

    async def test_registration_with_alerts_turned_off_is_not_notified(self):
        _, payload = await self._create(notify=False, extension="1044")

        self.assertFalse(payload["welcome"]["sent"])
        self.assertEqual(payload["welcome"]["reason"], "notifications_disabled")

    async def test_disabled_flag_keeps_the_registration_silent(self):
        self.config.welcome_email_enabled = False

        _, payload = await self._create(extension="1045")

        self.assertFalse(payload["welcome"]["sent"])
        self.assertEqual(payload["welcome"]["reason"], "disabled")
        self.assertEqual(self.alerts.recent_events(), [])


if __name__ == "__main__":
    unittest.main()
