import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

import names
from directory import DirectoryStore
from web import create_app


class _FakeAlerts:
    def __init__(self):
        self.calls = 0

    def enqueue_test(self):
        self.calls += 1
        return {
            "id": "event-1",
            "extension": "TESTE",
            "kind": "test",
            "status": "queued",
            "change": "test",
            "created_at": 100,
            "updated_at": 100,
            "sent_count": 0,
            "total_recipients": 1,
            "failed_count": 0,
            "attempts": 0,
            "last_error": None,
        }


class AlertTestEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.alerts = _FakeAlerts()
        app = create_app(None, None, SimpleNamespace(), alerts=self.alerts)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_requires_explicit_action_header(self):
        response = await self.client.post("/api/alerts/test", json={"confirm": True})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.alerts.calls, 0)

    async def test_rejects_cross_origin_request(self):
        response = await self.client.post(
            "/api/alerts/test",
            json={"confirm": True},
            headers={"X-PulsoPBX-Action": "test-alert", "Origin": "https://example.invalid"},
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(self.alerts.calls, 0)

    async def test_queues_confirmed_test(self):
        response = await self.client.post(
            "/api/alerts/test",
            json={"confirm": True},
            headers={"X-PulsoPBX-Action": "test-alert"},
        )
        payload = await response.json()
        self.assertEqual(response.status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event"]["kind"], "test")
        self.assertEqual(self.alerts.calls, 1)


class DashboardAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = SimpleNamespace(
            dashboard_auth_enabled=True,
            dashboard_username="pulsopbx",
            dashboard_password="secret",
            demo_mode=False,
        )
        app = create_app(None, None, config)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        credentials = base64.b64encode(b"pulsopbx:secret").decode("ascii")
        self.auth_headers = {"Authorization": f"Basic {credentials}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_status_requires_basic_authentication(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])

    async def test_valid_basic_authentication_allows_dashboard(self):
        response = await self.client.get("/", headers=self.auth_headers)
        self.assertEqual(response.status, 200)

    async def test_authenticated_brand_assets_are_served(self):
        for path in ("/assets/pulsopbx-logo.png", "/favicon.ico"):
            with self.subTest(path=path):
                response = await self.client.get(path, headers=self.auth_headers)
                content = await response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers["Content-Type"].startswith("image/"))
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=86400")
                self.assertGreater(len(content), 1_000)

    async def test_health_endpoint_does_not_expose_people_or_require_auth(self):
        response = await self.client.get("/api/health")
        payload = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"ready": True, "ami": "not_configured"})


class _FakeTracker:
    def snapshot(self):
        return [
            {
                "extension": "1001",
                "online": True,
                "pending_status": None,
                "since": 100,
            }
        ]

    def retain_extensions(self, extensions):
        return []


class ResponsibleManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.names_file = Path(self.temporary.name) / "ramais_nomes.json"
        self.names_patch = patch.object(names, "NAMES_FILE", self.names_file)
        self.names_patch.start()
        names._cache.update({"mtime": None, "data": {}})
        self.miko_patch = patch(
            "mikopbx_api.get_cached_profiles",
            return_value={
                "1001": {"nome": "Ana - Financeiro", "email": "ana@miko.example.com"}
            },
        )
        self.miko_patch.start()
        config = SimpleNamespace(
            dashboard_auth_enabled=False,
            demo_mode=False,
            responsibles_admin_enabled=True,
            responsibles_admin_password="senha-administrativa-segura",
        )
        app = create_app(_FakeTracker(), None, config)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.headers = {
            "X-PulsoPBX-Admin": "senha-administrativa-segura",
            "X-PulsoPBX-Action": "manage-responsibles",
        }

    async def asyncTearDown(self):
        await self.client.close()
        self.miko_patch.stop()
        self.names_patch.stop()
        self.temporary.cleanup()

    async def test_requires_admin_password(self):
        response = await self.client.get(
            "/api/admin/responsibles",
            headers={"X-PulsoPBX-Action": "manage-responsibles"},
        )
        self.assertEqual(response.status, 401)

    async def test_cloudflare_request_cannot_reach_management(self):
        response = await self.client.get(
            "/api/admin/responsibles",
            headers={**self.headers, "CF-Ray": "public-request"},
        )
        self.assertEqual(response.status, 404)

    async def test_local_email_overrides_mikopbx_and_can_be_cleared(self):
        response = await self.client.put(
            "/api/admin/responsibles/1001",
            json={"email": "ana.local@example.com", "sector": "Televendas", "notify": True},
            headers=self.headers,
        )
        self.assertEqual(response.status, 200)

        response = await self.client.get("/api/admin/responsibles", headers=self.headers)
        payload = await response.json()
        self.assertEqual(payload["responsibles"][0]["email"], "ana.local@example.com")
        self.assertEqual(payload["responsibles"][0]["sector"], "Televendas")
        self.assertEqual(payload["responsibles"][0]["source"], "local")

        response = await self.client.delete(
            "/api/admin/responsibles/1001", headers=self.headers
        )
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/admin/responsibles", headers=self.headers)
        payload = await response.json()
        self.assertEqual(payload["responsibles"][0]["email"], "ana@miko.example.com")
        self.assertEqual(payload["responsibles"][0]["sector"], "Televendas")
        self.assertEqual(payload["responsibles"][0]["source"], "mikopbx")

    async def test_rejects_sector_longer_than_80_characters(self):
        response = await self.client.put(
            "/api/admin/responsibles/1001",
            json={"email": "ana@example.com", "sector": "A" * 81, "notify": True},
            headers=self.headers,
        )

        self.assertEqual(response.status, 400)

    async def test_rejects_invalid_email_and_cross_origin_write(self):
        response = await self.client.put(
            "/api/admin/responsibles/1001",
            json={"email": "invalido", "notify": True},
            headers=self.headers,
        )
        self.assertEqual(response.status, 400)
        response = await self.client.put(
            "/api/admin/responsibles/1001",
            json={"email": "ana@example.com", "notify": True},
            headers={**self.headers, "Origin": "https://example.invalid"},
        )
        self.assertEqual(response.status, 403)


class DirectoryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = DirectoryStore(Path(self.temporary.name) / "pulsopbx.db")
        self.directory.initialize()
        self.directory.synchronize_mikopbx(
            {
                "1001": {
                    "nome": "Financeiro - Thayse",
                    "email": "financeiro@example.com",
                }
            }
        )
        config = SimpleNamespace(
            dashboard_auth_enabled=False,
            demo_mode=False,
            responsibles_admin_enabled=True,
            responsibles_admin_password="senha-administrativa-segura",
        )
        app = create_app(_FakeTracker(), None, config, directory=self.directory)
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

    async def test_internal_directory_and_excel_exports_are_available(self):
        response = await self.client.get("/api/directory")
        payload = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["people"][0]["name"], "Thayse")
        self.assertEqual(payload["people"][0]["sector"], "Financeiro")

        response = await self.client.get("/api/directory/export/extensions.xlsx")
        content = await response.read()
        self.assertEqual(response.status, 200)
        self.assertTrue(content.startswith(b"PK"))
        self.assertIn("lista-ramais-atualizada.xlsx", response.headers["Content-Disposition"])

    async def test_cloudflare_cannot_read_directory_or_export(self):
        for path in ("/api/directory", "/api/directory/export/emails.xlsx"):
            with self.subTest(path=path):
                response = await self.client.get(path, headers={"CF-Ray": "public-request"})
                self.assertEqual(response.status, 404)

    async def test_admin_can_create_update_and_archive_person(self):
        response = await self.client.post(
            "/api/admin/directory",
            json={
                "name": "Eduardo",
                "role": "Assistente de TI Júnior",
                "sector": "T.I.",
                "extension": "8008",
                "email": "assistente@example.com",
                "active": True,
                "notify": True,
            },
            headers=self.headers,
        )
        payload = await response.json()
        self.assertEqual(response.status, 201)
        person_id = payload["person"]["id"]

        response = await self.client.put(
            f"/api/admin/directory/{person_id}",
            json={**payload["person"], "role": "Analista de TI", "active": False},
            headers=self.headers,
        )
        updated = await response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(updated["person"]["role"], "Analista de TI")
        self.assertFalse(updated["person"]["active"])

        response = await self.client.get("/api/admin/directory", headers=self.headers)
        admin = await response.json()
        self.assertTrue(any(change["action"] == "archive" for change in admin["changes"]))


class StaticManagementUiTests(unittest.TestCase):
    def test_collaborator_registration_exposes_email_sector_and_search(self):
        html = (Path(__file__).parent.parent / "static" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("Cadastro de colaboradores", html)
        self.assertIn('id="responsible-email"', html)
        self.assertIn('id="responsible-sector-input"', html)
        self.assertIn('id="responsibles-with-sector"', html)
        self.assertIn("row.email ||", html)
        self.assertIn("row.sector ||", html)
