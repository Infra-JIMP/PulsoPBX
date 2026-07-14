import base64
import unittest
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

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
