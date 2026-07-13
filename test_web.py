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


if __name__ == "__main__":
    unittest.main()
